"""Hora de pared real: anclas del puente y tramos contiguos.

El buffer de muestras de un estudio es continuo por construcción — cada lote se
pega al anterior. La grabación no lo es. Estos tests cubren las tres cosas que
rompen la correspondencia entre las dos (`INTEGRACION.md` §5) y el contrato de
las cabeceras nuevas (`docs/integracion-ingesta-con-horario.md`).
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models.study_timeline_segment import StudyTimelineSegment, TimeSyncSource
from app.modules.ingest import timeline
from app.modules.ingest.processing import process_batch
from app.modules.studies.studies_service import compact_study_pyramid
from tests.ingest_helpers import OMIT, build_frames, post_frames

HOUR_MS = 3_600_000


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


async def _segments(db, study_id) -> list[StudyTimelineSegment]:
    return list(
        (
            await db.scalars(
                select(StudyTimelineSegment)
                .where(StudyTimelineSegment.study_id == study_id)
                .order_by(StudyTimelineSegment.ordinal)
            )
        ).all()
    )


async def _ingest(client, db, device, api_key, frames, **headers):
    body = (await post_frames(client, device, api_key, frames, **headers)).json()
    await process_batch(db, body["batchId"])
    return body


# --------------------------------------------------------------------------- #
# El ancla
# --------------------------------------------------------------------------- #


async def test_the_bridge_epoch_beats_our_reception_time(
    client, s3, db, make_patient, make_device
) -> None:
    """Con el epoch del puente, la latencia del pedido queda fuera de la hora.

    El camino viejo derivaba el ancla de nuestra hora de recepción, así que los
    5,1 s de mediana que midió Biomédica se le sumaban a la hora de la medición.
    Acá el puente dice que su reloj marcaba una hora concreta cuando el equipo
    llevaba `uptime_ms` prendido, y esa pareja no la puede tocar la red.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    uptime_ms = 2 * HOUR_MS
    bridge_epoch = _now_ms() - 30_000  # el puente sincronizó hace 30 s

    body = await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(900),
        uptime_ms=uptime_ms,
        bridge_epoch_ms=bridge_epoch,
    )

    segment = (await _segments(db, body["studyId"]))[0]
    assert segment.boot_epoch_ms == bridge_epoch - uptime_ms
    assert segment.anchor_source is TimeSyncSource.NTP
    assert segment.anchor_uncertainty_ms == 45


async def test_without_the_headers_the_old_anchor_still_works(
    client, s3, db, make_patient, make_device, monkeypatch
) -> None:
    """El camino viejo sigue vivo para poder apagar el modo estricto.

    Hoy sale prendido, pero el interruptor existe justamente para volver atrás
    sin desplegar si el puente de Biomédica queda sin hora, así que el ancla
    derivada de nuestra recepción tiene que seguir funcionando.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    monkeypatch.setattr(settings, "ingest_require_time_sync", False)

    body = await _ingest(client, db, device, api_key, build_frames(900), bridge_epoch_ms=OMIT)

    segment = (await _segments(db, body["studyId"]))[0]
    assert segment.anchor_source is TimeSyncSource.SERVER_RECEIVE
    # Derivada de nuestra recepción: no puede valer más que la latencia del pedido.
    assert segment.anchor_uncertainty_ms is not None and segment.anchor_uncertainty_ms > 1_000


async def test_the_headers_are_required_when_strict_mode_is_on(
    client, s3, db, make_patient, make_device, monkeypatch
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    # Explícito aunque hoy sea el default: lo que se prueba es el modo estricto.
    monkeypatch.setattr(settings, "ingest_require_time_sync", True)

    response = await post_frames(client, device, api_key, build_frames(900), bridge_epoch_ms=OMIT)

    assert response.status_code == 422
    assert response.json()["code"] == "DEVICE_TIME_SYNC_REQUIRED"


async def test_an_absurd_epoch_is_rejected(client, s3, db, make_patient, make_device) -> None:
    """Un puente con SNTP roto no puede fechar un estudio en 1970.

    Una hora absurda no se distingue después de una real, así que la única
    oportunidad de atajarla es acá.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(900), bridge_epoch_ms=0)

    assert response.status_code == 422
    assert response.json()["code"] == "DEVICE_TIME_INVALID"


async def test_a_failed_sync_is_accepted_but_marked(
    client, s3, db, make_patient, make_device
) -> None:
    """Perder señal por no saber la hora sería peor que archivarla aproximada."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(900),
        bridge_epoch_ms=_now_ms(),
        sync_source="none",
        sync_uncertainty_ms=3_600_000,
    )

    segment = (await _segments(db, body["studyId"]))[0]
    assert segment.anchor_source is TimeSyncSource.NONE
    assert segment.anchor_uncertainty_ms == 3_600_000


# --------------------------------------------------------------------------- #
# Las tres cosas que abren un tramo nuevo
# --------------------------------------------------------------------------- #


async def test_a_reboot_opens_a_new_segment_with_its_own_anchor(
    client, s3, db, make_patient, make_device
) -> None:
    """El chaleco se quedó sin batería en medio de la medición.

    Es el caso que motiva todo esto: `t0Ms` vuelve a cero y sin un ancla nueva
    toda la señal posterior quedaría fechada como si fuera continuación de la
    anterior.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    base = _now_ms() - 6 * HOUR_MS
    # 1500 muestras a 500 Hz: la última cae en (1500-1)*2 ms ≈ 3 s desde el
    # arranque. El tramo grabado ocupa eso, y el hueco se cuenta desde ahí.
    first_span_ms = 3_000

    first = await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(1500, boot_id=3, first_seq=0),
        uptime_ms=HOUR_MS,
        bridge_epoch_ms=base + HOUR_MS,
    )
    # Estuvo apagado dos horas y volvió: bootId nuevo, uptime desde cero.
    second = await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(1500, boot_id=4, first_seq=first["lastAcceptedSeq"] + 1, t0_ms=0),
        uptime_ms=60_000,
        bridge_epoch_ms=base + first_span_ms + 2 * HOUR_MS + 60_000,
    )

    assert second["studyId"] == first["studyId"]
    segments = await _segments(db, first["studyId"])
    assert [s.ordinal for s in segments] == [0, 1]
    assert segments[0].boot_id == 3
    assert segments[1].boot_id == 4
    # El hueco entre los dos tramos es real y mide lo que estuvo apagado.
    gap_ms = segments[1].start_epoch_ms - segments[0].end_epoch_ms
    assert gap_ms == pytest.approx(2 * HOUR_MS, abs=60_000)
    # Y los dos tramos son contiguos en el buffer de muestras, que es justo el
    # motivo por el que el hueco no se podía ver antes.
    assert segments[1].start_sample_index == segments[0].sample_count


async def test_millis_wraparound_opens_a_new_segment_without_a_reboot(
    client, s3, db, make_patient, make_device
) -> None:
    """A los 49,7 días `t0Ms` retrocede SIN que cambie el bootId.

    Hay que distinguirlo del reinicio: el equipo no se reinició, solo dio la
    vuelta su contador. Sin tramo nuevo, la hora de todo lo posterior se iría 49
    días para atrás.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    near_wrap = 0xFFFFFFFF - 5_000
    now = _now_ms()

    first = await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(1500, boot_id=2, t0_ms=near_wrap),
        uptime_ms=near_wrap,
        bridge_epoch_ms=now,
    )
    await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(1500, boot_id=2, first_seq=first["lastAcceptedSeq"] + 1, t0_ms=0),
        # `millis()` dio la vuelta, así que el uptime que reporta el puente
        # también: no sigue creciendo, vuelve a empezar cerca de cero.
        uptime_ms=(near_wrap + 10_000) % 2**32,
        bridge_epoch_ms=now + 10_000,
    )

    segments = await _segments(db, first["studyId"])
    assert len(segments) == 2, "el wraparound tiene que cortar el tramo"
    assert segments[0].boot_id == segments[1].boot_id == 2
    assert segments[1].start_epoch_ms >= segments[0].start_epoch_ms


async def test_a_long_silence_opens_a_new_segment(
    client, s3, db, make_patient, make_device
) -> None:
    """Fuera del alcance del WiFi, o el chaleco sacado un rato.

    No hay reinicio ni wraparound: el `t0Ms` simplemente salta. Sin esta regla el
    eje pegaría los dos bordes y el médico vería una grabación continua que nunca
    existió.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    now = _now_ms()

    first = await _ingest(
        client, db, device, api_key, build_frames(1500, boot_id=1), bridge_epoch_ms=now
    )
    # Cuatro horas después, mismo arranque, sin nada grabado en el medio.
    await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(1500, boot_id=1, first_seq=first["lastAcceptedSeq"] + 1, t0_ms=4 * HOUR_MS),
        uptime_ms=3_600_000 + 4 * HOUR_MS,
        bridge_epoch_ms=now + 4 * HOUR_MS,
    )

    segments = await _segments(db, first["studyId"])
    assert len(segments) == 2
    gap_ms = segments[1].start_epoch_ms - segments[0].end_epoch_ms
    assert gap_ms == pytest.approx(4 * HOUR_MS, abs=60_000)


async def test_batches_of_the_same_run_extend_one_segment(
    client, s3, db, make_patient, make_device
) -> None:
    """Lo normal: el chaleco sube cada tanto y la grabación no se corta."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    now = _now_ms()
    frames = build_frames(3000, boot_id=1)
    third = len(frames) // 3

    body = await _ingest(
        client, db, device, api_key, frames[:third], uptime_ms=HOUR_MS, bridge_epoch_ms=now
    )
    for index in (1, 2):
        chunk = frames[third * index : third * (index + 1)] if index == 1 else frames[third * 2 :]
        await _ingest(
            client,
            db,
            device,
            api_key,
            chunk,
            uptime_ms=HOUR_MS + index * 1_000,
            bridge_epoch_ms=now + index * 1_000,
        )

    segments = await _segments(db, body["studyId"])
    assert len(segments) == 1, "una grabación sin cortes es UN tramo"
    assert segments[0].sample_count == 3000


# --------------------------------------------------------------------------- #
# El manifest
# --------------------------------------------------------------------------- #


async def test_the_manifest_carries_the_timeline_and_absolute_annotations(
    client, s3, db, make_patient, make_device, make_user, as_user
) -> None:
    from app.db.models.user import UserRole

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    now = _now_ms()

    body = await _ingest(
        client, db, device, api_key, build_frames(2000, boot_id=1), bridge_epoch_ms=now
    )
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/studies/{body['studyId']}/ecg/manifest")
    manifest = response.json()

    assert manifest["formatVersion"] == 3
    assert len(manifest["timeline"]) == 1
    segment = manifest["timeline"][0]
    assert segment["ordinal"] == 0
    assert segment["anchorSource"] == "ntp"
    assert segment["startSampleIndex"] == 0
    assert segment["sampleCount"] == 2000
    assert segment["endEpochMs"] > segment["startEpochMs"]


# --------------------------------------------------------------------------- #
# Backfill de lo ya ingerido
# --------------------------------------------------------------------------- #


async def test_the_backfill_rebuilds_the_timeline_of_an_existing_study(
    client, s3, db, make_patient, make_device
) -> None:
    """Los estudios ya archivados recuperan sus huecos sin reprocesar señal.

    `ecg_batch` ya guardaba el ancla y los `seq` de cada lote, así que los tramos
    se pueden reconstruir de ahí. La precisión es la vieja y queda declarada como
    tal; lo que se gana es que un chaleco apagado cuatro horas deje de ser
    invisible.
    """
    from sqlalchemy import delete

    from app.scripts.backfill_timeline import _batches, _segments_for

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    now = _now_ms()

    body = await _ingest(
        client, db, device, api_key, build_frames(1500, boot_id=1), bridge_epoch_ms=now
    )
    study_id = body["studyId"]
    await _ingest(
        client,
        db,
        device,
        api_key,
        build_frames(1500, boot_id=1, first_seq=body["lastAcceptedSeq"] + 1, t0_ms=4 * HOUR_MS),
        uptime_ms=3_600_000 + 4 * HOUR_MS,
        bridge_epoch_ms=now + 4 * HOUR_MS,
    )
    live = await _segments(db, study_id)
    assert len(live) == 2, "la ingesta en vivo ya vio el hueco"

    # Se borra la línea de tiempo y se reconstruye solo desde los lotes.
    await db.execute(delete(StudyTimelineSegment).where(StudyTimelineSegment.study_id == study_id))
    await db.flush()

    study = await db.get(type(live[0]).study.property.mapper.class_, study_id)
    rebuilt = _segments_for(study, await _batches(db, study.id))

    assert len(rebuilt) == len(live)
    assert [s.start_sample_index for s in rebuilt] == [s.start_sample_index for s in live]
    assert rebuilt[0].anchor_source is TimeSyncSource.SERVER_RECEIVE
    # El hueco sobrevive a la reconstrucción, que es el punto del backfill.
    gap_ms = rebuilt[1].start_epoch_ms - rebuilt[0].end_epoch_ms
    assert gap_ms == pytest.approx(4 * HOUR_MS, abs=120_000)


async def test_closing_a_study_compacts_the_pyramid_levels(
    client, s3, db, make_patient, make_device, make_user, as_user
) -> None:
    """Al cerrar, cada nivel queda en un objeto único.

    Los chunks existen para que el trabajo por lote no crezca con el estudio. Un
    estudio cerrado ya no crece, así que es el momento de pagar la fusión una vez
    y dejarle al visor una sola URL por nivel en vez de una por lote.
    """
    from app.db.models.study import Study
    from app.db.models.user import UserRole

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    now = _now_ms()
    frames = build_frames(24_000, boot_id=1)
    per_batch = len(frames) // 6

    body = await _ingest(client, db, device, api_key, frames[:per_batch], bridge_epoch_ms=now)
    for index in range(1, 6):
        await _ingest(
            client,
            db,
            device,
            api_key,
            frames[per_batch * index : per_batch * (index + 1)],
            uptime_ms=3_600_000 + index * 1_000,
            bridge_epoch_ms=now + index * 1_000,
        )

    study = await db.get(Study, body["studyId"])
    assert study is not None
    await db.refresh(study)
    assert any(len(level["chunks"]) > 1 for level in study.ecg_pyramid_levels), (
        "seis lotes tienen que dejar varios chunks por nivel"
    )

    as_user(await make_user(UserRole.ADMIN))
    response = await client.post(f"/studies/{body['studyId']}/complete")
    assert response.status_code == 200, response.text

    # La compactación es ~900 GET a S3 con `boto3` sincrónico, así que no corre
    # adentro del request: el cierre la agenda y contesta. Se la invoca acá con
    # la sesión del test por lo mismo que `process_batch` — la tarea real abre
    # otra conexión y no vería esta transacción.
    await compact_study_pyramid(db, uuid.UUID(body["studyId"]))
    await db.refresh(study)
    for level in study.ecg_pyramid_levels:
        assert len(level["chunks"]) == 1, "al cerrar, un nivel es un solo objeto"
        assert level["chunks"][0]["pointCount"] == level["pointCount"]


# --------------------------------------------------------------------------- #
# La regla del hueco, aislada del resto
# --------------------------------------------------------------------------- #


class _FakeSegment:
    """Lo mínimo de `StudyTimelineSegment` que mira `starts_new_segment`."""

    def __init__(self, boot_id: int, last_t0_ms: int, boot_epoch_ms: int) -> None:
        self.boot_id = boot_id
        self.last_t0_ms = last_t0_ms
        self.boot_epoch_ms = boot_epoch_ms
        self.anchor_slope_ppm = 0
        self.end_epoch_ms = boot_epoch_ms + last_t0_ms


class _FakeBatch:
    def __init__(self, epoch_anchor_ms: int, boot_id: int) -> None:
        self.epoch_anchor_ms = epoch_anchor_ms
        self.boot_id = boot_id


def _timing(boot_id: int, first_t0_ms: int, span_ms: int) -> timeline.BatchTiming:
    return timeline.BatchTiming(
        boot_id=boot_id,
        first_t0_ms=first_t0_ms,
        last_t0_ms=first_t0_ms + span_ms - 2,
        last_end_t0_ms=first_t0_ms + span_ms,
        first_seq=0,
        last_seq=0,
    )


@pytest.mark.parametrize("latency_ms", [0, 5_100, 6_300, 22_800])
def test_the_latency_of_a_request_does_not_split_a_continuous_recording(
    latency_ms: int,
) -> None:
    """El ancla vieja lleva la latencia del pedido adentro; el hueco no.

    Con `ingest_require_time_sync` apagado el ancla es `recepción − uptime`, así
    que cada lote trae adentro la latencia de SU pedido: 5,1 s de mediana y picos
    de 22,8 s según el informe de Biomédica del 8/9/2026. Comparar el arranque de
    un lote contra el final del anterior comparaba esas dos latencias y no la
    grabación, así que un pico partía en dos una corrida perfectamente continua
    — y como el pico se va tan rápido como vino, el tramo siguiente arrancaba
    ANTES de que terminara el anterior, con el eje del visor yendo para atrás.

    Con el mismo `bootId` y `t0Ms` monótono el hueco se mide en el reloj del
    equipo, que las dos puntas de la resta comparten.
    """
    span_ms = 10 * 60_000
    boot_epoch_ms = 1_757_000_000_000
    previous = _FakeSegment(boot_id=3, last_t0_ms=span_ms, boot_epoch_ms=boot_epoch_ms)
    # El lote siguiente arranca exactamente donde terminó el anterior en tiempo
    # del equipo, pero su ancla llega corrida por la latencia.
    batch = _FakeBatch(epoch_anchor_ms=boot_epoch_ms + latency_ms, boot_id=3)

    opens = timeline.starts_new_segment(previous, batch, _timing(3, span_ms, span_ms))

    assert opens is False, f"una latencia de {latency_ms} ms partió una grabación continua"


def test_a_real_silence_still_opens_a_new_segment() -> None:
    """El contrapeso del test de arriba: el hueco de verdad tiene que cortar."""
    span_ms = 10 * 60_000
    boot_epoch_ms = 1_757_000_000_000
    previous = _FakeSegment(boot_id=3, last_t0_ms=span_ms, boot_epoch_ms=boot_epoch_ms)
    batch = _FakeBatch(epoch_anchor_ms=boot_epoch_ms, boot_id=3)

    # Cuatro horas sin grabar, con el equipo despierto: `t0Ms` salta.
    silent_start = span_ms + 4 * HOUR_MS
    assert timeline.starts_new_segment(previous, batch, _timing(3, silent_start, span_ms))
