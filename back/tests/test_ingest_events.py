"""Eventos derivados: lo que el médico va a mirar.

No son las 43 millones de muestras de un estudio de 24 h, son estos.
"""

import numpy as np
from sqlalchemy import select

from app.core.s3 import get_object
from app.db.models.alert import Alert
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.study import Study
from app.ml.decompression import (
    FLAG_ADC_SATURATED,
    FLAG_EVENT_MARKER,
    FLAG_LEAD_OFF,
    FLAG_RLD_OFF,
    FLAG_SQI_SHIFT,
    SQ_BAD,
)
from app.ml.status_flags import STATUS_FLAG_BACKLOG_OVERFLOW, STATUS_FLAG_CORRUPT_FRAME
from app.modules.ingest.processing import derive_events, process_batch
from tests.ingest_helpers import build_frames, build_frames_with_flag_span, post_frames


async def _ingest(client, db, device, api_key, frames):
    body = (await post_frames(client, device, api_key, frames)).json()
    await process_batch(db, body["batchId"])
    return body


async def _events(db) -> list[ECGEvent]:
    return list((await db.scalars(select(ECGEvent))).all())


def _kinds(events: list[ECGEvent]) -> list[str]:
    return [e.event_metadata["kind"] for e in events if e.event_metadata]


async def test_symptom_marker_creates_an_event_and_an_alert(
    client, s3, db, make_patient, make_device
) -> None:
    """El paciente apretó el botón. Es un hallazgo, y el médico tiene que verlo."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1200, span=(400, 404), flags=FLAG_EVENT_MARKER)

    await _ingest(client, db, device, api_key, frames)

    events = await _events(db)
    markers = [e for e in events if e.event_metadata["kind"] == "symptom_marker"]
    assert len(markers) == 1
    assert markers[0].event_type == ECGEventType.OTHER
    assert markers[0].severity == ECGEventSeverity.HIGH

    alerts = list((await db.scalars(select(Alert))).all())
    assert len(alerts) == 1
    assert alerts[0].patient_id == patient.id
    assert alerts[0].event_id == markers[0].id


async def test_lead_off_is_marked_but_the_samples_are_kept(
    client, s3, db, make_patient, make_device
) -> None:
    """Regla explícita de §7.3: NO descartar las muestras con LEAD_OFF.

    Son parte del registro. Hay que marcarlas, no borrarlas.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1500, span=(200, 900), flags=FLAG_LEAD_OFF)

    body = await _ingest(client, db, device, api_key, frames)

    assert "lead_off" in _kinds(await _events(db))
    study = await db.get(Study, body["studyId"])
    assert study is not None
    signal = np.frombuffer(get_object(study.ecg_segments[0]["key"]), dtype="<f4")
    assert signal.size == study.samples_count == 1500


async def test_rld_off_does_not_create_an_invalidating_event(
    client, s3, db, make_patient, make_device
) -> None:
    """§4.5 regla 2: perder la tierra degrada el modo común, pero el par RA-LL
    sigue midiendo una diferencia de potencial real. No invalida nada."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1500, span=(200, 1200), flags=FLAG_RLD_OFF)

    await _ingest(client, db, device, api_key, frames)

    kinds = _kinds(await _events(db))
    assert "lead_off" not in kinds
    assert "adc_saturated" not in kinds


async def test_unanalyzable_sqi_is_low_severity_noise(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1500, span=(300, 1100), flags=SQ_BAD << FLAG_SQI_SHIFT)

    await _ingest(client, db, device, api_key, frames)

    events = [e for e in await _events(db) if e.event_metadata["kind"] == "sqi_unanalyzable"]
    assert events
    assert events[0].event_type == ECGEventType.NOISE
    assert events[0].severity == ECGEventSeverity.LOW


async def test_adc_saturation_is_recorded(client, s3, db, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1500, span=(100, 800), flags=FLAG_ADC_SATURATED)

    await _ingest(client, db, device, api_key, frames)

    assert "adc_saturated" in _kinds(await _events(db))


async def test_a_short_blip_does_not_create_an_event(
    client, s3, db, make_patient, make_device
) -> None:
    """Un electrodo que rebota unos milisegundos no es un hallazgo.

    Sin umbral, un contacto intermitente llenaría la base de eventos y el médico
    dejaría de mirarlos.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1500, span=(400, 410), flags=FLAG_LEAD_OFF)

    await _ingest(client, db, device, api_key, frames)

    assert "lead_off" not in _kinds(await _events(db))


async def test_event_offsets_are_absolute_within_the_study(
    client, s3, db, make_patient, make_device
) -> None:
    """El offset de un evento del segundo lote no puede arrancar de cero."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    first = build_frames(1500)
    await _ingest(client, db, device, api_key, first)
    second = build_frames_with_flag_span(
        1500, span=(200, 900), flags=FLAG_LEAD_OFF, first_seq=len(first)
    )
    await _ingest(client, db, device, api_key, second)

    lead_off = [e for e in await _events(db) if e.event_metadata["kind"] == "lead_off"]
    assert len(lead_off) == 1
    assert lead_off[0].event_metadata["startSampleIndex"] >= 1500
    assert lead_off[0].timestamp_in_recording >= 3.0  # 1500 muestras a 500 SPS


async def test_events_count_matches_the_rows(client, s3, db, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames_with_flag_span(1500, span=(200, 900), flags=FLAG_LEAD_OFF)

    body = await _ingest(client, db, device, api_key, frames)

    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.events_count == len(await _events(db))


async def test_internal_gap_is_reported_with_its_exact_length(
    client, s3, db, make_patient, make_device
) -> None:
    """Un hueco no es una línea isoeléctrica: es información clínica."""
    from app.ml.decompression import STEP_MS
    from tests.frame_builder import Sample, encode_samples

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[i % 100]) for i in range(600)]
    for sample in samples[300:]:
        sample.timestamp_ms += 4  # 4 ms que no existen en ningún lado

    await _ingest(client, db, device, api_key, encode_samples(samples))

    gaps = [e for e in await _events(db) if e.event_metadata["kind"] == "internal_gap"]
    assert gaps
    assert gaps[0].severity == ECGEventSeverity.MEDIUM


async def test_a_clean_batch_produces_no_events(client, s3, db, make_patient, make_device):
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    await _ingest(client, db, device, api_key, build_frames(1500))

    assert await _events(db) == []


# --------------------------------------------------------------------------- #
# `derive_events` como función pura
# --------------------------------------------------------------------------- #


def _batch_from(flags: list[int]):
    from app.modules.ingest.processing import _DecodedBatch

    return _DecodedBatch(
        signal_mV=np.zeros(len(flags), dtype="<f4"),
        flags=np.array(flags, dtype=np.uint8),
        frames=[],
    )


def test_contiguous_runs_produce_a_single_event() -> None:
    flags = [0] * 100 + [FLAG_LEAD_OFF] * 800 + [0] * 100

    events = derive_events(_batch_from(flags), sample_rate=500)

    lead_off = [e for e in events if e.kind == "lead_off"]
    assert len(lead_off) == 1
    assert lead_off[0].start_sample == 100
    assert lead_off[0].length_samples == 800


def test_two_separate_runs_produce_two_events() -> None:
    flags = [FLAG_LEAD_OFF] * 300 + [0] * 50 + [FLAG_LEAD_OFF] * 300

    events = [e for e in derive_events(_batch_from(flags), sample_rate=500) if e.kind == "lead_off"]

    assert len(events) == 2


def test_every_symptom_press_is_its_own_event() -> None:
    flags = [0] * 10 + [FLAG_EVENT_MARKER] * 2 + [0] * 10 + [FLAG_EVENT_MARKER] * 2

    events = [e for e in derive_events(_batch_from(flags), sample_rate=500)]

    assert len(events) == 2
    assert all(e.alert_message for e in events)


# --------------------------------------------------------------------------- #
# Pérdida de señal: lo que NO está en ninguna muestra
# --------------------------------------------------------------------------- #


async def test_a_backlog_overflow_gap_becomes_an_event_and_an_alert(
    client, s3, db, make_patient, make_device
) -> None:
    """El hueco de `INTEGRACION.md` §4.6, visible para el médico.

    Aceptar el salto destraba la ingesta, pero aceptarlo en silencio sería peor
    que el deadlock: el estudio quedaría con horas faltantes y nada que lo diga.
    §9.1 lo pide explícito — "registro de cada overflow con su hora de pared: es
    un hueco en el estudio y el médico tiene que verlo".
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000, first_seq=0)

    await _ingest(client, db, device, api_key, frames[:2])
    # El log circular dio la vuelta: faltan dos tramas y el bootId no cambió.
    body = (
        await post_frames(
            client, device, api_key, frames[4:6], status_flags=STATUS_FLAG_BACKLOG_OVERFLOW
        )
    ).json()
    await process_batch(db, body["batchId"])

    overflows = [e for e in await _events(db) if e.event_metadata["kind"] == "backlog_overflow"]
    assert len(overflows) == 1
    event = overflows[0]
    assert event.severity is ECGEventSeverity.HIGH
    assert event.event_metadata["gapFrames"] == 2
    assert event.event_metadata["cause"] == "device_confirmed"
    # Ocupa cero muestras: el buffer del estudio es continuo por construcción y
    # una banda con ancho taparía señal real (ver `signal_loss_events`).
    assert event.event_metadata["sampleCount"] == 0

    alert = (await db.execute(select(Alert).where(Alert.event_id == event.id))).scalar_one()
    assert "perdió señal" in alert.message


async def test_a_gap_without_the_status_bit_is_recorded_as_inferred(
    client, s3, db, make_patient, make_device
) -> None:
    """Sin el bit del equipo el hueco sigue siendo real; lo que falta es la causa."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000, first_seq=0)

    await _ingest(client, db, device, api_key, frames[:2])
    await _ingest(client, db, device, api_key, frames[4:6])

    overflows = [e for e in await _events(db) if e.event_metadata["kind"] == "backlog_overflow"]
    assert len(overflows) == 1
    assert overflows[0].event_metadata["cause"] == "inferred"


async def test_reprocessing_does_not_duplicate_the_gap_event(
    client, s3, db, make_patient, make_device
) -> None:
    """Todo sale de columnas del lote, así que reprocesar da lo mismo."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000, first_seq=0)

    await _ingest(client, db, device, api_key, frames[:2])
    body = (await post_frames(client, device, api_key, frames[4:6])).json()
    await process_batch(db, body["batchId"])
    await process_batch(db, body["batchId"])

    overflows = [e for e in await _events(db) if e.event_metadata["kind"] == "backlog_overflow"]
    assert len(overflows) == 1


async def test_a_contiguous_batch_produces_no_gap_event(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000, first_seq=0)

    await _ingest(client, db, device, api_key, frames[:2])
    await _ingest(client, db, device, api_key, frames[2:4])

    assert [e for e in await _events(db) if e.event_metadata["kind"] == "backlog_overflow"] == []


async def test_a_crc_discard_reported_by_the_device_becomes_an_event(
    client, s3, db, make_patient, make_device
) -> None:
    """Bit 3 del STATUS: el equipo descartó una trama por CRC.

    Es un hueco real en el registro del que no queda rastro en las tramas que sí
    llegaron, porque la que falta nunca se archivó.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (
        await post_frames(
            client, device, api_key, build_frames(1500), status_flags=STATUS_FLAG_CORRUPT_FRAME
        )
    ).json()
    await process_batch(db, body["batchId"])

    corrupt = [e for e in await _events(db) if e.event_metadata["kind"] == "corrupt_frame"]
    assert len(corrupt) == 1
    assert corrupt[0].severity is ECGEventSeverity.MEDIUM
