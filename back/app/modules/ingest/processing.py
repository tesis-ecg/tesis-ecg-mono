"""Procesamiento asíncrono de un lote de tramas ya archivado.

Corre después de haber respondido el ACK. Toma las tramas crudas de S3 y deja
el estudio listo para el visor:

    tramas 256 B  →  float32 mV (segmento)  →  envolvente min/max  →  pirámide
                                              └→  ecg_event / alert

**Segmentos y no un blob que crece.** S3 no soporta append: mantener un
`ecg.f32` monolítico obligaría a reescribir el objeto entero cada hora (173 MB
en la hora 24, ~4 GB de tráfico por estudio). Cada lote escribe solo lo suyo.

**Pirámide incremental exacta.** Todos los buckets son múltiplos de 16, así que
los niveles gruesos son reducciones min/max de una envolvente base con
bucket=16 — no hay que volver a decodificar nada para rehacerlos.
"""

import hashlib
import uuid
from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.s3 import get_object, list_keys, put_object
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.study import Study, StudyStatus
from app.ml.decompression import (
    FLAG_ADC_SATURATED,
    FLAG_EVENT_MARKER,
    FLAG_LEAD_OFF,
    FLAG_RLD_OFF,
    FLAG_SQI_MASK,
    FLAG_SQI_SHIFT,
    SQ_BAD,
    DecodedFrame,
    FrameError,
    decode_frame,
    iter_frames,
)
from app.modules.ingest import ingest_repository as repo

logger = structlog.get_logger(__name__)

#: Mismos buckets que usa `seed_demo`, para que el visor no tenga que
#: distinguir un estudio seedeado de uno ingestado.
PYRAMID_BUCKETS = (16, 64, 256, 1024, 4096, 16384)
BASE_BUCKET = PYRAMID_BUCKETS[0]

#: El firmware entrega µV (int32, DC-acoplado); el visor grafica mV.
UV_PER_MV = 1000.0


def segment_key(study_id: uuid.UUID, first_seq: int) -> str:
    return f"studies/{study_id}/segments/{first_seq:012d}.f32"


def envelope_key(study_id: uuid.UUID, first_seq: int) -> str:
    return f"studies/{study_id}/envelopes/{first_seq:012d}.f32"


def envelope_prefix(study_id: uuid.UUID) -> str:
    return f"studies/{study_id}/envelopes/"


def level_key(study_id: uuid.UUID, bucket: int) -> str:
    return f"studies/{study_id}/ecg.minmax.{bucket}.f32"


# --------------------------------------------------------------------------- #
# Decodificación
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _DecodedBatch:
    #: Canal 0 en mV. El visor grafica una derivación; el resto queda en las
    #: tramas crudas de S3 para cuando el visor soporte dos.
    signal_mV: np.ndarray
    flags: np.ndarray
    frames: list[DecodedFrame]

    @property
    def n_samples(self) -> int:
        return int(self.signal_mV.size)


def decode_batch(payload: bytes) -> _DecodedBatch:
    frames = [decode_frame(raw) for raw in iter_frames(payload)]
    if not frames:
        raise FrameError("lote vacío")
    signal = np.concatenate([f.raw_uV[0] for f in frames]).astype(np.float32) / UV_PER_MV
    flags = np.concatenate([f.flags for f in frames])
    return _DecodedBatch(signal_mV=signal.astype("<f4"), flags=flags, frames=frames)


# --------------------------------------------------------------------------- #
# Pirámide
# --------------------------------------------------------------------------- #


def build_envelope(signal: np.ndarray, bucket: int = BASE_BUCKET) -> tuple[np.ndarray, np.ndarray]:
    """`(envolvente, resto)`.

    Devuelve solo los buckets **completos**; las muestras que sobran vuelven
    como resto para que las anteponga el lote siguiente. Sin eso, cada lote
    empezaría su propio bucket y los buckets del estudio dejarían de estar
    alineados a la grilla del estudio: 24 lotes acumulan hasta 384 muestras
    (0,77 s) de deriva en el eje X.
    """
    complete = (signal.size // bucket) * bucket
    if complete == 0:
        return np.empty(0, dtype="<f4"), signal
    blocks = signal[:complete].reshape(-1, bucket)
    envelope = np.empty(blocks.shape[0] * 2, dtype="<f4")
    envelope[0::2] = blocks.min(axis=1)
    envelope[1::2] = blocks.max(axis=1)
    return envelope, signal[complete:]


def reduce_envelope(base: np.ndarray, factor: int) -> np.ndarray:
    """Agrupa `factor` pares min/max en uno. Exacto: min y max son asociativos."""
    pairs = base.size // 2
    complete = (pairs // factor) * factor
    chunks: list[np.ndarray] = []
    if complete:
        mins = base[0 : complete * 2 : 2].reshape(-1, factor).min(axis=1)
        maxs = base[1 : complete * 2 : 2].reshape(-1, factor).max(axis=1)
        merged = np.empty(mins.size * 2, dtype="<f4")
        merged[0::2] = mins
        merged[1::2] = maxs
        chunks.append(merged)
    if pairs > complete:  # cola parcial: entra igual, no se descarta señal
        tail = base[complete * 2 :]
        chunks.append(np.array([tail[0::2].min(), tail[1::2].max()], dtype="<f4"))
    if not chunks:
        return np.empty(0, dtype="<f4")
    return np.concatenate(chunks).astype("<f4")


def _object_meta(key: str, payload: bytes, **extra: object) -> dict[str, object]:
    return {
        "key": key,
        "byteLength": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **extra,
    }


def rebuild_pyramid(study: Study) -> list[dict[str, object]]:
    """Rehace los niveles gruesos leyendo las envolventes, no la señal.

    Para 24 h son ~21 MB de envolventes contra ~173 MB de señal, y evita volver
    a decodificar tramas que ya se decodificaron una vez.
    """
    parts = [get_object(key) for key in list_keys(envelope_prefix(study.id))]
    if not parts:
        return []
    base = np.frombuffer(b"".join(parts), dtype="<f4")
    total_samples = study.samples_count

    levels: list[dict[str, object]] = []
    for bucket in PYRAMID_BUCKETS:
        envelope = base if bucket == BASE_BUCKET else reduce_envelope(base, bucket // BASE_BUCKET)
        if envelope.size == 0 or envelope.size >= total_samples:
            continue  # un nivel que no comprime no vale el objeto en S3
        payload = envelope.tobytes()
        key = level_key(study.id, bucket)
        put_object(key, payload)
        levels.append(
            _object_meta(
                key,
                payload,
                samplesPerBucket=bucket,
                pointCount=int(envelope.size),
            )
        )
    return levels


# --------------------------------------------------------------------------- #
# Eventos derivados
# --------------------------------------------------------------------------- #


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Tramos `(inicio, largo)` donde `mask` es verdadera."""
    if mask.size == 0 or not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(s), int(e - s)) for s, e in zip(edges[0::2], edges[1::2], strict=True)]


@dataclass(frozen=True)
class DerivedEvent:
    kind: str
    event_type: ECGEventType
    severity: ECGEventSeverity
    start_sample: int
    length_samples: int
    alert_message: str | None = None


#: Umbral para no inundar la base con eventos de un electrodo que rebota. Medio
#: segundo a 500 SPS.
MIN_RUN_SAMPLES = 250


def derive_events(batch: _DecodedBatch, sample_rate: int) -> list[DerivedEvent]:
    """Lo que el médico va a mirar, que no son las 43 M de muestras.

    Las reglas de interpretación son las de `INTEGRACION.md` §4.5:
    `LEAD_OFF` invalida el tramo (pero las muestras **se conservan**, marcadas),
    `RLD_OFF` no invalida nada, y con SQI = 1 no se cuentan latidos.
    """
    flags = batch.flags
    events: list[DerivedEvent] = []

    # Marca de síntoma del paciente: es un hallazgo, no ruido. Cada pulsación es
    # un evento propio aunque dure una sola muestra.
    for start, length in _runs((flags & FLAG_EVENT_MARKER) != 0):
        events.append(
            DerivedEvent(
                kind="symptom_marker",
                event_type=ECGEventType.OTHER,
                severity=ECGEventSeverity.HIGH,
                start_sample=start,
                length_samples=length,
                alert_message="El paciente marcó un síntoma.",
            )
        )

    for start, length in _runs((flags & FLAG_LEAD_OFF) != 0):
        if length < MIN_RUN_SAMPLES:
            continue
        events.append(
            DerivedEvent(
                kind="lead_off",
                event_type=ECGEventType.NOISE,
                severity=ECGEventSeverity.MEDIUM,
                start_sample=start,
                length_samples=length,
            )
        )

    for start, length in _runs(((flags & FLAG_SQI_MASK) >> FLAG_SQI_SHIFT) == SQ_BAD):
        if length < MIN_RUN_SAMPLES:
            continue
        events.append(
            DerivedEvent(
                kind="sqi_unanalyzable",
                event_type=ECGEventType.NOISE,
                severity=ECGEventSeverity.LOW,
                start_sample=start,
                length_samples=length,
            )
        )

    # RLD_OFF NO entra: degrada el rechazo de modo común, pero el par RA-LL
    # sigue midiendo una diferencia de potencial real (§4.5, regla 2).
    saturated = (flags & FLAG_ADC_SATURATED) != 0
    for start, length in _runs(saturated & ((flags & FLAG_RLD_OFF) == 0)):
        if length < MIN_RUN_SAMPLES:
            continue
        events.append(
            DerivedEvent(
                kind="adc_saturated",
                event_type=ECGEventType.NOISE,
                severity=ECGEventSeverity.LOW,
                start_sample=start,
                length_samples=length,
            )
        )

    # Huecos internos: la trama declara más duración de la que tendría si no
    # faltara ninguna muestra. Un hueco no es una línea isoeléctrica.
    offset = 0
    for frame in batch.frames:
        gap_ms = frame.info.internal_gap_ms
        if gap_ms > 0:
            events.append(
                DerivedEvent(
                    kind="internal_gap",
                    event_type=ECGEventType.OTHER,
                    severity=ECGEventSeverity.MEDIUM,
                    start_sample=offset,
                    length_samples=int(gap_ms * sample_rate / 1000),
                )
            )
        if frame.info.close_reason != 0:
            events.append(
                DerivedEvent(
                    kind=f"close_reason_{frame.info.close_reason}",
                    event_type=ECGEventType.OTHER,
                    severity=ECGEventSeverity.LOW,
                    start_sample=offset,
                    length_samples=frame.info.n_samples,
                )
            )
        offset += frame.info.n_samples

    return events


async def _persist_events(
    db: AsyncSession,
    batch: ECGBatch,
    study: Study,
    events: list[DerivedEvent],
    start_sample_index: int,
    sample_rate: int,
) -> int:
    for derived in events:
        absolute = start_sample_index + derived.start_sample
        event = ECGEvent(
            batch_id=batch.id,
            event_type=derived.event_type,
            severity=derived.severity,
            timestamp_in_recording=absolute / sample_rate,
            duration_seconds=derived.length_samples / sample_rate,
            event_metadata={
                "kind": derived.kind,
                "studyId": str(study.id),
                "startSampleIndex": absolute,
                "sampleCount": derived.length_samples,
                "bootId": batch.boot_id,
            },
        )
        db.add(event)
        await db.flush()

        if derived.alert_message is not None:
            db.add(
                Alert(
                    patient_id=study.patient_id,
                    event_id=event.id,
                    severity=AlertSeverity[derived.severity.name],
                    message=derived.alert_message,
                )
            )
    return len(events)


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #


async def _process_one_batch(db: AsyncSession, study: Study, batch: ECGBatch) -> tuple[int, int]:
    """Procesa un lote con el estudio ya bloqueado; no maneja la transacción."""
    if batch.frames_s3_key is None:
        raise RuntimeError("El lote no tiene tramas archivadas.")
    batch.processing_status = ProcessingStatus.PROCESSING
    await db.flush()

    decoded = decode_batch(get_object(batch.frames_s3_key))
    sample_rate = study.sample_rate or 500

    # --- Segmento ---------------------------------------------------------- #
    start_sample_index = study.samples_count
    payload = decoded.signal_mV.tobytes()
    key = segment_key(study.id, batch.first_seq or 0)
    put_object(key, payload)

    segments = [segment for segment in study.ecg_segments if segment.get("key") != key]
    segments.append(
        _object_meta(
            key,
            payload,
            startSampleIndex=start_sample_index,
            sampleCount=decoded.n_samples,
        )
    )
    segments.sort(key=lambda item: int(item["startSampleIndex"]))
    study.ecg_segments = segments
    study.samples_count = start_sample_index + decoded.n_samples

    # --- Envolvente con carry de alineación -------------------------------- #
    carry = (
        np.frombuffer(study.ecg_envelope_carry, dtype="<f4")
        if study.ecg_envelope_carry
        else np.empty(0, dtype="<f4")
    )
    envelope, remainder = build_envelope(np.concatenate([carry, decoded.signal_mV]))
    if envelope.size:
        put_object(envelope_key(study.id, batch.first_seq or 0), envelope.tobytes())
    study.ecg_envelope_carry = remainder.tobytes() if remainder.size else None
    study.ecg_pyramid_levels = rebuild_pyramid(study)

    # La duración administrativa conserva reloj de pared, pero una tarea que
    # perdió la carrera contra complete/cancel no puede reabrir ni reescribir el
    # cierre clínico.
    last = decoded.frames[-1].info
    anchor = batch.epoch_anchor_ms or 0
    wall_clock_ms = int(
        (anchor + last.t0_ms + last.duration_ms) - study.started_at.timestamp() * 1000
    )
    samples_ms = int(study.samples_count * 1000 / sample_rate)
    if study.status is StudyStatus.IN_PROGRESS:
        study.duration_ms = max(study.duration_ms or 0, wall_clock_ms, samples_ms)
        study.ended_at = None

    if any(frame.info.simulated for frame in decoded.frames):
        study.is_simulated = True

    created = await _persist_events(
        db,
        batch,
        study,
        derive_events(decoded, sample_rate),
        start_sample_index,
        sample_rate,
    )
    study.events_count += created
    batch.num_samples = decoded.n_samples
    batch.processing_status = ProcessingStatus.DONE
    batch.processing_error = None
    return decoded.n_samples, created


async def process_batch(db: AsyncSession, batch_id: uuid.UUID) -> None:
    """Drena en orden todos los lotes pendientes del estudio solicitado.

    El lock del estudio hace que dos tareas concurrentes reconsulten los estados
    en serie; la segunda no vuelve a anexar lo que la primera terminó.
    """
    requested = await repo.get_batch(db, batch_id)
    if requested is None:
        await logger.awarning("process_batch_missing", batch_id=str(batch_id))
        return
    if requested.processing_status == ProcessingStatus.DONE:
        return
    if requested.study_id is None:
        requested.processing_status = ProcessingStatus.FAILED
        requested.processing_error = "El lote no tiene estudio asociado."
        await db.commit()
        return

    failed_batch_id = batch_id
    try:
        study = await repo.get_study_for_update(db, requested.study_id)
        if study is None:
            raise RuntimeError("el estudio del lote no existe")

        processed: list[tuple[ECGBatch, int, int]] = []
        for pending in await repo.list_batches_to_process(db, study.id):
            failed_batch_id = pending.id
            samples, events = await _process_one_batch(db, study, pending)
            processed.append((pending, samples, events))

        await db.commit()
        for done, samples, events in processed:
            await logger.ainfo(
                "process_batch_done",
                batch_id=str(done.id),
                study_id=str(study.id),
                samples=samples,
                events=events,
            )
    except Exception as error:  # noqa: BLE001 — el estado del lote tiene que reflejarlo
        await db.rollback()
        failed = await repo.get_batch(db, failed_batch_id)
        if failed is not None:
            failed.processing_status = ProcessingStatus.FAILED
            failed.processing_error = str(error)[:1024]
            await db.commit()
        await logger.aexception(
            "process_batch_failed",
            requested_batch_id=str(batch_id),
            failed_batch_id=str(failed_batch_id),
        )


async def process_batch_task(batch_id: uuid.UUID) -> None:
    """Entrypoint del `BackgroundTasks`: abre su propia sesión.

    La sesión del request ya está cerrada cuando esto corre.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        await process_batch(session, batch_id)
