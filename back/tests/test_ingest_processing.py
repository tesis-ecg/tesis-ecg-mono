"""Procesamiento en background: señal decodificada, segmentos y pirámide."""

import hashlib
import random
from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import select

from app.core.s3 import get_object, list_keys
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.study import Study, StudyStatus
from app.modules.ingest.processing import (
    BASE_BUCKET,
    build_envelope,
    envelope_prefix,
    process_batch,
    reduce_envelope,
)
from tests.ingest_helpers import build_frames, post_frames


async def _ingest_and_process(client, db, device, api_key, frames):
    body = (await post_frames(client, device, api_key, frames)).json()
    await process_batch(db, body["batchId"])
    return body


# --------------------------------------------------------------------------- #
# Envolvente y reducción: aritmética pura, sin base ni S3
# --------------------------------------------------------------------------- #


def test_envelope_holds_only_complete_buckets() -> None:
    signal = np.arange(40, dtype="<f4")

    envelope, remainder = build_envelope(signal, bucket=16)

    assert envelope.size == 2 * 2  # dos buckets completos
    assert remainder.size == 8
    assert envelope.tolist() == [0.0, 15.0, 16.0, 31.0]
    assert remainder.tolist() == list(range(32, 40))


def test_envelope_of_a_short_signal_is_all_remainder() -> None:
    envelope, remainder = build_envelope(np.arange(9, dtype="<f4"), bucket=16)

    assert envelope.size == 0
    assert remainder.size == 9


def test_reduce_envelope_is_an_exact_min_max_reduction() -> None:
    base = np.array([1, 5, -2, 3, 0, 9, 4, 4], dtype="<f4")  # 4 pares

    reduced = reduce_envelope(base, factor=2)

    assert reduced.tolist() == [-2.0, 5.0, 0.0, 9.0]


def test_reduce_envelope_keeps_a_partial_tail() -> None:
    """La cola parcial entra igual: no se descarta señal del final del estudio."""
    base = np.array([1, 5, -2, 3, 7, 8], dtype="<f4")  # 3 pares, factor 2

    reduced = reduce_envelope(base, factor=2)

    assert reduced.tolist() == [-2.0, 5.0, 7.0, 8.0]


# --------------------------------------------------------------------------- #
# Pipeline completo
# --------------------------------------------------------------------------- #


async def test_processing_writes_a_segment_and_marks_the_batch_done(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = await _ingest_and_process(client, db, device, api_key, build_frames(1500))

    batch = await db.get(ECGBatch, body["batchId"])
    study = await db.get(Study, body["studyId"])
    assert batch is not None and study is not None
    assert batch.processing_status == ProcessingStatus.DONE
    assert batch.processing_error is None
    assert len(study.ecg_segments) == 1

    segment = study.ecg_segments[0]
    payload = get_object(segment["key"])
    assert len(payload) == segment["byteLength"] == study.samples_count * 4
    assert hashlib.sha256(payload).hexdigest() == segment["sha256"]
    assert segment["startSampleIndex"] == 0
    assert segment["sampleCount"] == study.samples_count


async def test_signal_is_stored_as_float32_millivolts(
    client, s3, db, make_patient, make_device
) -> None:
    """El firmware entrega µV int32; el visor grafica mV float32."""
    from app.ml.decompression import STEP_MS
    from tests.frame_builder import Sample, encode_samples

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    values_uV = [1_000, -2_500, 0, 750, 300_000]
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(values_uV)]

    body = await _ingest_and_process(client, db, device, api_key, encode_samples(samples))

    study = await db.get(Study, body["studyId"])
    assert study is not None
    stored = np.frombuffer(get_object(study.ecg_segments[0]["key"]), dtype="<f4")
    assert stored.tolist() == pytest.approx([v / 1000 for v in values_uV])


async def test_pyramid_levels_are_exact_reductions_of_the_signal(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = await _ingest_and_process(client, db, device, api_key, build_frames(4000))

    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.ecg_pyramid_levels, "un estudio con 4000 muestras tiene que tener niveles"

    signal = np.frombuffer(get_object(study.ecg_segments[0]["key"]), dtype="<f4")
    for level in study.ecg_pyramid_levels:
        bucket = level["samplesPerBucket"]
        stored = np.frombuffer(get_object(level["key"]), dtype="<f4")
        assert stored.size == level["pointCount"]
        assert len(get_object(level["key"])) == level["byteLength"]
        # Cada bucket contiene el min y el max reales de su tramo.
        for index in range(stored.size // 2):
            chunk = signal[index * bucket : (index + 1) * bucket]
            if chunk.size == 0:
                continue
            assert stored[index * 2] == pytest.approx(chunk.min())
            assert stored[index * 2 + 1] == pytest.approx(chunk.max())


async def test_levels_that_do_not_compress_are_skipped(
    client, s3, db, make_patient, make_device
) -> None:
    """Un nivel con más puntos que la señal no vale el objeto en S3."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = await _ingest_and_process(client, db, device, api_key, build_frames(400))

    study = await db.get(Study, body["studyId"])
    assert study is not None
    for level in study.ecg_pyramid_levels:
        assert level["pointCount"] < study.samples_count


# --------------------------------------------------------------------------- #
# Carry de alineación entre lotes
# --------------------------------------------------------------------------- #


async def test_buckets_stay_aligned_to_the_study_grid_across_batches(
    client, s3, db, make_patient, make_device
) -> None:
    """Sin el carry, cada lote empezaría su propio bucket.

    Con lotes cuyo largo no es múltiplo de 16, la deriva se acumula: 24 lotes
    llegan a 384 muestras (0,77 s) de corrimiento en el eje X.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000)
    third = len(frames) // 3

    body_1 = await _ingest_and_process(client, db, device, api_key, frames[:third])
    study = await db.get(Study, body_1["studyId"])
    assert study is not None
    assert study.samples_count % BASE_BUCKET != 0, "el test necesita un lote desalineado"
    assert study.ecg_envelope_carry is not None

    await _ingest_and_process(client, db, device, api_key, frames[third : third * 2])
    await _ingest_and_process(client, db, device, api_key, frames[third * 2 :])
    await db.refresh(study)

    # La señal completa del estudio, concatenando los segmentos en orden.
    signal = np.concatenate(
        [np.frombuffer(get_object(s["key"]), dtype="<f4") for s in study.ecg_segments]
    )
    assert signal.size == study.samples_count

    base = np.concatenate(
        [
            np.frombuffer(get_object(key), dtype="<f4")
            for key in list_keys(envelope_prefix(study.id))
        ]
    )
    expected, _ = build_envelope(signal, bucket=BASE_BUCKET)
    assert base.tolist() == expected.tolist()


async def test_segments_are_contiguous_and_ordered(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000)
    half = len(frames) // 2

    body = await _ingest_and_process(client, db, device, api_key, frames[:half])
    await _ingest_and_process(client, db, device, api_key, frames[half:])

    study = await db.get(Study, body["studyId"])
    assert study is not None
    offset = 0
    for segment in study.ecg_segments:
        assert segment["startSampleIndex"] == offset
        offset += segment["sampleCount"]
    assert offset == study.samples_count


async def test_requesting_the_second_batch_processes_the_study_in_sequence_order(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000)
    half = len(frames) // 2
    first = (await post_frames(client, device, api_key, frames[:half])).json()
    second = (await post_frames(client, device, api_key, frames[half:])).json()

    await process_batch(db, second["batchId"])

    batches = list(
        (
            await db.scalars(
                select(ECGBatch)
                .where(ECGBatch.study_id == first["studyId"])
                .order_by(ECGBatch.first_seq)
            )
        ).all()
    )
    study = await db.get(Study, first["studyId"])
    assert study is not None
    assert [batch.processing_status for batch in batches] == [
        ProcessingStatus.DONE,
        ProcessingStatus.DONE,
    ]
    assert [segment["startSampleIndex"] for segment in study.ecg_segments] == [
        0,
        batches[0].num_samples,
    ]
    assert study.samples_count == sum(batch.num_samples for batch in batches)


@pytest.mark.parametrize("terminal_status", [StudyStatus.COMPLETED, StudyStatus.CANCELLED])
async def test_pending_processing_never_reopens_a_terminal_study(
    terminal_status, client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(1500))).json()
    study = await db.get(Study, body["studyId"])
    assert study is not None
    ended_at = datetime.now(UTC)
    study.status = terminal_status
    study.ended_at = ended_at
    study.duration_ms = 12_345
    await db.commit()

    await process_batch(db, body["batchId"])
    await db.refresh(study)

    assert study.status is terminal_status
    assert study.ended_at == ended_at
    assert study.duration_ms == 12_345
    assert study.samples_count > 0


async def test_a_failed_earlier_batch_blocks_later_batches_until_retry(
    client, s3, db, make_patient, make_device, monkeypatch
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000)
    half = len(frames) // 2
    first_body = (await post_frames(client, device, api_key, frames[:half])).json()
    second_body = (await post_frames(client, device, api_key, frames[half:])).json()
    first = await db.get(ECGBatch, first_body["batchId"])
    second = await db.get(ECGBatch, second_body["batchId"])
    assert first is not None and second is not None and first.frames_s3_key is not None

    real_get = get_object

    def fail_first(key: str) -> bytes:
        if key == first.frames_s3_key:
            raise RuntimeError("primer lote temporalmente ilegible")
        return real_get(key)

    monkeypatch.setattr("app.modules.ingest.processing.get_object", fail_first)
    await process_batch(db, second.id)
    await db.refresh(first)
    await db.refresh(second)
    assert first.processing_status is ProcessingStatus.FAILED
    assert second.processing_status is ProcessingStatus.PENDING

    monkeypatch.undo()
    await process_batch(db, second.id)
    await db.refresh(first)
    await db.refresh(second)
    assert first.processing_status is ProcessingStatus.DONE
    assert second.processing_status is ProcessingStatus.DONE


async def test_sample_count_and_duration_accumulate(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000)
    half = len(frames) // 2

    body = await _ingest_and_process(client, db, device, api_key, frames[:half])
    study = await db.get(Study, body["studyId"])
    assert study is not None
    after_first = study.samples_count
    duration_first = study.duration_ms or 0

    await _ingest_and_process(client, db, device, api_key, frames[half:])
    await db.refresh(study)

    assert study.samples_count > after_first
    assert (study.duration_ms or 0) > duration_first
    assert study.ended_at is None  # sigue en curso


async def _transmit_window(client, db, device, api_key, pending, rng, drop_pct):
    """Un envío go-back-N: se manda la ventana sin confirmar y se libera con el ACK.

    Reproduce lo que hace el simulador. La pérdida se sortea **solo en el primer
    intento** de cada trama: es transitoria, y la retransmisión pasa.
    """
    body = []
    for entry in pending:
        first_try = entry["attempts"] == 0
        entry["attempts"] += 1
        if first_try and rng.random() * 100 < drop_pct:
            continue
        body.append(entry["frame"])
    if not body:
        return 0

    ack = (await post_frames(client, device, api_key, body)).json()
    if ack["batchId"] is not None:
        await process_batch(db, ack["batchId"])

    last = ack["lastAcceptedSeq"]
    freed = 0
    while pending and last is not None and pending[0]["seq"] <= last:
        pending.pop(0)
        freed += 1
    return freed


async def test_a_lossy_link_with_retransmission_still_completes_the_study(
    client, s3, db, make_patient, make_device
) -> None:
    """El escenario que motivó el arreglo, de punta a punta.

    Tres lotes con 30 % de tramas perdidas en el primer envío. Mientras el equipo
    retransmita lo que el ACK no confirmó, el estudio tiene que quedar completo
    hasta la última trama grabada, con los segmentos contiguos y sin que ningún
    lote pise a otro en S3.

    Con el bug viejo esto daba un estudio de un puñado de muestras: el simulador
    avanzaba su cursor las tramas generadas, el hueco no se llenaba nunca y todo
    lote posterior arrancaba más adelante que el cursor del backend.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    rng = random.Random(7)
    pending: list[dict] = []
    next_seq = 0

    for _ in range(3):
        frames = build_frames(1500, first_seq=next_seq)
        pending.extend(
            {"seq": next_seq + i, "frame": frame, "attempts": 0} for i, frame in enumerate(frames)
        )
        next_seq += len(frames)
        await _transmit_window(client, db, device, api_key, pending, rng, 30)

    for _ in range(4):  # drenado final
        if (
            not pending
            or await _transmit_window(client, db, device, api_key, pending, rng, 30) == 0
        ):
            break

    assert pending == []

    batches = list((await db.scalars(select(ECGBatch).order_by(ECGBatch.first_seq))).all())
    study = await db.get(Study, batches[0].study_id)
    assert study is not None
    await db.refresh(study)

    # Ningún lote comparte `first_seq`: es la invariante que sostiene los nombres
    # de los objetos del estudio en S3.
    assert len({batch.first_seq for batch in batches}) == len(batches)
    assert all(batch.processing_status is ProcessingStatus.DONE for batch in batches)

    # El estudio llega hasta la última trama grabada. Las primeras se pueden
    # perder —el backend fija su cursor en la primera que le llega y lo anterior
    # nadie sabe que existió—, pero del final no puede faltar nada.
    assert batches[-1].last_seq == next_seq - 1
    assert batches[0].first_seq < 5

    # Y la señal quedó contigua: un lote que pisa a otro se ve acá como un salto.
    offset = 0
    for segment in study.ecg_segments:
        assert segment["startSampleIndex"] == offset
        offset += segment["sampleCount"]
    assert offset == study.samples_count
    assert len(study.ecg_segments) == len(batches)
    assert study.samples_count == sum(batch.num_samples for batch in batches)


# --------------------------------------------------------------------------- #
# Idempotencia y fallas
# --------------------------------------------------------------------------- #


async def test_reprocessing_a_done_batch_changes_nothing(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = await _ingest_and_process(client, db, device, api_key, build_frames(1500))
    study = await db.get(Study, body["studyId"])
    assert study is not None
    before = (study.samples_count, len(study.ecg_segments), study.events_count)

    await process_batch(db, body["batchId"])
    await db.refresh(study)

    assert (study.samples_count, len(study.ecg_segments), study.events_count) == before


async def test_a_failure_marks_the_batch_and_leaves_the_frames_replayable(
    client, s3, db, make_patient, make_device, monkeypatch
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(1500))).json()

    def _boom(key: str, payload: bytes) -> None:
        raise RuntimeError("S3 caído")

    monkeypatch.setattr("app.modules.ingest.processing.put_object", _boom)
    await process_batch(db, body["batchId"])

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    await db.refresh(batch)
    assert batch.processing_status == ProcessingStatus.FAILED
    assert "S3" in (batch.processing_error or "")
    # Las tramas siguen intactas: la tarea es re-ejecutable sin pedirle nada al equipo.
    assert batch.frames_s3_key is not None
    assert len(get_object(batch.frames_s3_key)) > 0

    monkeypatch.undo()
    await process_batch(db, body["batchId"])
    await db.refresh(batch)
    assert batch.processing_status == ProcessingStatus.DONE


async def test_a_retry_after_failure_does_not_duplicate_segments(
    client, s3, db, make_patient, make_device, monkeypatch
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(1500))).json()

    calls = {"n": 0}
    real_put = __import__("app.modules.ingest.processing", fromlist=["put_object"]).put_object

    def _fail_second(key: str, payload: bytes) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("corte a mitad")
        real_put(key, payload)

    monkeypatch.setattr("app.modules.ingest.processing.put_object", _fail_second)
    await process_batch(db, body["batchId"])
    monkeypatch.undo()

    await process_batch(db, body["batchId"])

    study = await db.get(Study, body["studyId"])
    assert study is not None
    await db.refresh(study)
    assert len(study.ecg_segments) == 1
    assert study.samples_count == study.ecg_segments[0]["sampleCount"]


async def test_a_batch_without_frames_fails_cleanly(client, s3, db, make_patient, make_device):
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(900))).json()
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    batch.frames_s3_key = None
    await db.flush()

    await process_batch(db, body["batchId"])

    await db.refresh(batch)
    assert batch.processing_status == ProcessingStatus.FAILED
    assert batch.processing_error


async def test_processing_a_missing_batch_is_a_noop(db, s3) -> None:
    import uuid

    await process_batch(db, uuid.uuid4())  # no debe explotar


async def test_duration_never_shrinks_when_the_device_clock_drifts(
    client, s3, db, make_patient, make_device
) -> None:
    """El oscilador del equipo deriva contra el del servidor.

    Cada lote trae su propia ancla (`recepción − uptime`). Si una ancla se corre
    para atrás, la duración calculada por reloj de pared sale menor que la
    anterior — o negativa. Antes eso la dejaba en 0 y el eje X del visor se
    colapsaba, dejando el estudio ilegible.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000)
    half = len(frames) // 2

    first = (await post_frames(client, device, api_key, frames[:half], uptime_ms=3_600_000)).json()
    await process_batch(db, first["batchId"])
    study = await db.get(Study, first["studyId"])
    assert study is not None
    after_first = study.duration_ms or 0
    assert after_first > 0

    # Segundo lote con un uptime desproporcionado respecto del tiempo real
    # transcurrido: es exactamente lo que produce una deriva de reloj.
    second = (
        await post_frames(client, device, api_key, frames[half:], uptime_ms=99_000_000)
    ).json()
    await process_batch(db, second["batchId"])
    await db.refresh(study)

    assert (study.duration_ms or 0) >= after_first
    # Y nunca menos de lo que implican las muestras guardadas.
    assert (study.duration_ms or 0) >= study.samples_count * 1000 // study.sample_rate


async def test_duration_reflects_a_real_gap_between_batches(
    s3, db, make_patient, make_device
) -> None:
    """Un Holter que estuvo horas sin conexión dura más de lo que suman sus
    muestras, y eso es información clínica que tiene que verse en el eje X.

    Se llama al service directamente porque hay que controlar `received_at`: el
    ancla es `recepción − uptime`, así que un hueco real solo se puede expresar
    moviendo las dos cosas de forma coherente. A través de la ruta, `received_at`
    es siempre "ahora" y el escenario sería físicamente imposible (el equipo
    ganaría 3 h de uptime en 0 segundos reales).
    """
    from datetime import UTC, datetime, timedelta

    from app.dependencies.device_dependencies import DeviceContext
    from app.modules.ingest.ingest_schemas import IngestFramesInput
    from app.modules.ingest.ingest_service import ingest_frames

    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    started = datetime.now(UTC)

    async def send(frames: list[bytes], uptime_ms: int, at: datetime) -> str:
        ack = await ingest_frames(
            DeviceContext(
                device=device, uptime_ms=uptime_ms, firmware_version="1.0.0", battery_pct=80
            ),
            IngestFramesInput(payload=b"".join(frames), received_at=at),
            db,
            background=None,
        )
        assert ack.batchId is not None
        await process_batch(db, ack.batchId)
        return str(ack.studyId)

    first = build_frames(1500)
    study_id = await send(first, uptime_ms=3_600_000, at=started)

    # Tres horas después, tanto en el reloj del equipo como en el del servidor.
    gapped = build_frames(1500, first_seq=len(first), t0_ms=10_800_000)
    await send(gapped, uptime_ms=14_400_000, at=started + timedelta(hours=3))

    study = await db.get(Study, study_id)
    assert study is not None
    await db.refresh(study)
    samples_ms = study.samples_count * 1000 // study.sample_rate
    assert samples_ms == 6_000  # 3000 muestras a 500 SPS
    # El hueco de 3 h está en la duración, no disuelto.
    assert (study.duration_ms or 0) > 3 * 3_600_000
