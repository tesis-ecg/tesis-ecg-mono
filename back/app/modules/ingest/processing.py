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

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import structlog
from asyncpg.exceptions import LockNotAvailableError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.s3 import get_object, put_object
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
from app.modules.ingest import timeline
from app.modules.patient_app.notifications_service import (
    anomaly_message,
    notify_patient_task,
)

logger = structlog.get_logger(__name__)

#: Mismos buckets que usa `seed_demo`, para que el visor no tenga que
#: distinguir un estudio seedeado de uno ingestado.
PYRAMID_BUCKETS = (16, 64, 256, 1024, 4096, 16384)
BASE_BUCKET = PYRAMID_BUCKETS[0]

#: Chunks por nivel antes de fundirlos en un objeto único. 24 acota los GET que
#: hace el visor sin volver la compactación tan frecuente que reintroduzca el
#: costo cuadrático que estamos sacando.
LEVEL_COMPACTION_THRESHOLD = 24

#: El firmware entrega µV (int32, DC-acoplado); el visor grafica mV.
UV_PER_MV = 1000.0


def segment_key(study_id: uuid.UUID, first_seq: int) -> str:
    return f"studies/{study_id}/segments/{first_seq:012d}.f32"


def envelope_key(study_id: uuid.UUID, first_seq: int) -> str:
    return f"studies/{study_id}/envelopes/{first_seq:012d}.f32"


def envelope_prefix(study_id: uuid.UUID) -> str:
    return f"studies/{study_id}/envelopes/"


def level_key(study_id: uuid.UUID, bucket: int) -> str:
    """Nivel compactado: un objeto único con todo el nivel."""
    return f"studies/{study_id}/ecg.minmax.{bucket}.f32"


def level_chunk_key(study_id: uuid.UUID, bucket: int, first_seq: int) -> str:
    """Tramo de un nivel aportado por UN lote.

    Los niveles se escriben por chunks y no como un objeto que se reescribe
    entero en cada lote. Reescribirlo era el origen de los `500` que reportó
    Biomédica: `rebuild_pyramid` leía de S3 **todas** las envolventes ya
    archivadas del estudio en cada lote (lote 1 leía un objeto, el lote 30 leía
    treinta) mientras tenía tomada la fila del estudio, y el POST siguiente
    moría esperando ese lock a los 15 s del `statement_timeout`.
    """
    return f"studies/{study_id}/levels/{bucket}/{first_seq:012d}.f32"


def level_chunk_prefix(study_id: uuid.UUID, bucket: int) -> str:
    return f"studies/{study_id}/levels/{bucket}/"


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


def reduce_envelope_exact(base: np.ndarray, factor: int) -> tuple[np.ndarray, np.ndarray]:
    """Como `reduce_envelope`, pero **sin cola parcial**: `(reducida, resto)`.

    La diferencia importa para los niveles por chunks. `reduce_envelope` cierra
    la cola en un bucket incompleto, que está bien cuando se reduce el estudio
    entero de una vez; hacerlo por lote produciría un bucket corto por lote y los
    niveles gruesos dejarían de estar alineados a la grilla del estudio. El resto
    vuelve como carry y lo antepone el lote siguiente — es exactamente lo que ya
    hace `build_envelope` con el bucket base.
    """
    pairs = base.size // 2
    complete = (pairs // factor) * factor
    if complete == 0:
        return np.empty(0, dtype="<f4"), base
    mins = base[0 : complete * 2 : 2].reshape(-1, factor).min(axis=1)
    maxs = base[1 : complete * 2 : 2].reshape(-1, factor).max(axis=1)
    merged = np.empty(mins.size * 2, dtype="<f4")
    merged[0::2] = mins
    merged[1::2] = maxs
    return merged.astype("<f4"), base[complete * 2 :]


def _object_meta(key: str, payload: bytes, **extra: object) -> dict[str, Any]:
    return {
        "key": key,
        "byteLength": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **extra,
    }


def _chunk_order(study: Study, bucket: int, key: str) -> tuple[int, str]:
    """Posición de un chunk dentro de su nivel.

    El objeto compactado va primero: contiene todo lo anterior a los chunks que
    se le anexaron después. El resto ordena por su clave, que lleva la `seq` con
    ceros a la izquierda para que ordenar por texto ordene por tiempo.
    """
    return (0, "") if key == level_key(study.id, bucket) else (1, key)


def _decode_carry(raw: str | None) -> np.ndarray:
    if not raw:
        return np.empty(0, dtype="<f4")
    return np.frombuffer(bytes.fromhex(str(raw)), dtype="<f4")


def append_level_chunks(
    study: Study, base_envelope: np.ndarray, first_seq: int
) -> list[dict[str, Any]]:
    """Anexa a cada nivel lo que aporta ESTE lote. Trabajo O(lote), no O(estudio).

    Todos los buckets de la pirámide son múltiplos de 16, así que un nivel de
    bucket `B` es la envolvente base reducida por `B/16`. Reducir solo la parte
    del lote que completa buckets, y arrastrar el resto como carry, da byte a
    byte lo mismo que reducir el estudio entero de una vez — min y max son
    asociativos — pero sin volver a leer nada de lo ya archivado.

    El nivel base (bucket 16) no escribe objeto propio: sus chunks **son** las
    envolventes que ya escribe el caller.
    """
    levels: list[dict[str, Any]] = list(study.ecg_pyramid_levels or [])
    by_bucket = {int(level["samplesPerBucket"]): level for level in levels}
    carry_state: dict[str, str] = dict(study.ecg_level_carry or {})

    for bucket in PYRAMID_BUCKETS:
        if bucket == BASE_BUCKET:
            chunk = base_envelope
            key = envelope_key(study.id, first_seq)
        else:
            factor = bucket // BASE_BUCKET
            combined = np.concatenate([_decode_carry(carry_state.get(str(bucket))), base_envelope])
            chunk, remainder = reduce_envelope_exact(combined, factor)
            carry_state[str(bucket)] = remainder.tobytes().hex() if remainder.size else ""
            key = level_chunk_key(study.id, bucket, first_seq)

        if chunk.size == 0:
            continue
        payload = chunk.tobytes()
        if bucket != BASE_BUCKET:
            put_object(key, payload)

        level = by_bucket.get(bucket)
        if level is None:
            level = {"samplesPerBucket": bucket, "pointCount": 0, "chunks": []}
            by_bucket[bucket] = level
            levels.append(level)
        chunks = [c for c in level.get("chunks", []) if c.get("key") != key]
        chunks.append(_object_meta(key, payload, pointCount=int(chunk.size)))
        # Ordenados y no en orden de llegada: el cliente concatena los chunks tal
        # como vienen, así que el orden ES la señal. Un lote reprocesado entra por
        # la deduplicación de arriba y se re-anexaría al final, dejando su tramo
        # fuera de lugar dentro del nivel. Es lo mismo que ya hace `ecg_segments`
        # con `startSampleIndex`.
        chunks.sort(key=lambda item: _chunk_order(study, bucket, str(item["key"])))
        level["chunks"] = chunks
        level["pointCount"] = sum(int(c["pointCount"]) for c in chunks)

    study.ecg_level_carry = carry_state
    # Un nivel que no comprime no vale los objetos que ocupa en S3.
    return [level for level in levels if int(level["pointCount"]) < max(study.samples_count, 1)]


def compact_level(study: Study, level: dict[str, Any]) -> dict[str, Any]:
    """Funde los chunks de un nivel en un objeto único.

    Los chunks acotan el trabajo por lote, pero acumulan objetos: un estudio de
    24 h que sube cada 10 minutos deja ~144 chunks por nivel, y el visor tendría
    que hacer 144 GET para pintar la vista general. Compactar es O(estudio), pero
    ocurre pocas veces —al cruzar el umbral y al cerrar el estudio— en vez de una
    vez por lote, que es justamente lo que rompía.
    """
    bucket = int(level["samplesPerBucket"])
    chunks = list(level.get("chunks", []))
    if len(chunks) <= 1:
        return level
    payload = b"".join(get_object(str(chunk["key"])) for chunk in chunks)
    key = level_key(study.id, bucket)
    put_object(key, payload)
    merged = _object_meta(key, payload, pointCount=len(payload) // 4)
    return {
        "samplesPerBucket": bucket,
        "pointCount": len(payload) // 4,
        "chunks": [merged],
    }


def compact_pyramid(study: Study, *, force: bool = False) -> list[dict[str, Any]]:
    """Compacta los niveles cuyo recuento de chunks cruzó el umbral."""
    return [
        compact_level(study, level)
        if force or len(level.get("chunks", [])) >= LEVEL_COMPACTION_THRESHOLD
        else level
        for level in (study.ecg_pyramid_levels or [])
    ]


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


#: Severidades que despiertan al paciente. Una `LOW` (medio segundo de un
#: electrodo que rebotó) no justifica una notificación, y la app la muestra
#: igual en Inicio cuando el paciente la abre.
_PUSHABLE = {ECGEventSeverity.HIGH: 1, ECGEventSeverity.CRITICAL: 2}


@dataclass(frozen=True)
class Pushable:
    """La alerta que se va a notificar, con lo que el push necesita saber.

    El `kind` viaja hasta acá porque el título del aviso lo nombra ("tu chaleco
    registró un ritmo irregular") y el formulario que abre lo encabeza. Deducirlo
    después, del lado del push, obligaría a releer el evento con la transacción
    ya cerrada.
    """

    rank: int
    alert_id: uuid.UUID
    kind: str


async def _persist_events(
    db: AsyncSession,
    batch: ECGBatch,
    study: Study,
    events: list[DerivedEvent],
    start_sample_index: int,
    sample_rate: int,
) -> tuple[int, Pushable | None]:
    """Persiste los eventos y devuelve `(cuántos, la alerta más severa a notificar)`.

    La alerta viaja hacia arriba en vez de notificarse acá porque todavía no se
    commiteó: mandarle al paciente un push con un `alertId` que después la
    transacción descarta lo dejaría tocando una notificación rota.
    """
    pushable: Pushable | None = None
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
            alert = Alert(
                patient_id=study.patient_id,
                event_id=event.id,
                kind=derived.kind,
                severity=AlertSeverity[derived.severity.name],
                message=derived.alert_message,
            )
            db.add(alert)
            await db.flush()
            rank = _PUSHABLE.get(derived.severity)
            # Un lote de 1 h puede traer varias anomalías. Se notifica una sola
            # —la más severa— para no vaciar la batería del celular ni saturar
            # al paciente con avisos que va a terminar silenciando.
            if rank is not None and (pushable is None or rank > pushable.rank):
                pushable = Pushable(rank=rank, alert_id=alert.id, kind=derived.kind)
    return len(events), pushable


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #


async def _place_on_timeline(
    db: AsyncSession,
    study: Study,
    batch: ECGBatch,
    decoded: _DecodedBatch,
    start_sample_index: int,
) -> None:
    """Abre o extiende el tramo al que pertenece este lote.

    `start_sample_index` es la posición del lote dentro del buffer empaquetado
    del estudio, que es lo que después permite traducir índice de muestra a hora
    de pared y al revés.
    """
    first = decoded.frames[0].info
    last = decoded.frames[-1].info
    timing = timeline.batch_timing(batch, first.t0_ms, last.t0_ms, last.duration_ms)

    current = await repo.get_last_timeline_segment(db, study.id)
    if current is None or timeline.starts_new_segment(current, batch, timing):
        ordinal = 0 if current is None else current.ordinal + 1
        await repo.add_timeline_segment(
            db,
            timeline.open_segment(
                study, batch, timing, ordinal, start_sample_index, decoded.n_samples
            ),
        )
        return

    # `list_boot_anchors` ya filtra las filas sin ancla completa, así que las dos
    # columnas están; el `or 0` es solo para el tipo.
    anchors = [
        (int(row.device_uptime_ms or 0), int(row.bridge_epoch_ms or 0))
        for row in await repo.list_boot_anchors(db, study.id, batch.boot_id, current.first_seq)
    ]
    timeline.extend_segment(current, batch, timing, decoded.n_samples, anchors)


async def _process_one_batch(
    db: AsyncSession, study: Study, batch: ECGBatch
) -> tuple[int, int, Pushable | None]:
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
    # Antes acá se llamaba `rebuild_pyramid`, que releía de S3 TODAS las
    # envolventes del estudio en cada lote. Es la causa de los `500` del informe
    # de Biomédica: crecía con el estudio y corría con la fila bloqueada.
    study.ecg_pyramid_levels = append_level_chunks(study, envelope, batch.first_seq or 0)
    study.ecg_pyramid_levels = compact_pyramid(study)

    # --- Línea de tiempo de pared ------------------------------------------ #
    await _place_on_timeline(db, study, batch, decoded, start_sample_index)

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

    created, pushable = await _persist_events(
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
    return decoded.n_samples, created, pushable


#: Intentos de tomar la fila del estudio antes de dejar el lote para más tarde.
#: El `lock_timeout` de 3 s existe para que un REQUEST falle rápido: el equipo lee
#: un 503 y reintenta. Acá no hay nadie esperando una respuesta, así que rendirse
#: al primer intento sería peor — el lote quedaría marcado `FAILED`, y un lote
#: `FAILED` solo se vuelve a mirar cuando llega otro lote del mismo estudio. Si el
#: chaleco ya terminó de subir, esa señal se queda archivada en S3 y sin procesar.
LOCK_ATTEMPTS = 4
LOCK_RETRY_SECONDS = 2.0


async def _lock_study(db: AsyncSession, study_id: uuid.UUID) -> Study | None:
    """Toma la fila del estudio, reintentando mientras esté tomada.

    La contención acá es esperada y transitoria: la ingesta del lote siguiente
    tiene la fila mientras confirma su ACK. No es una falla del lote.
    """
    for attempt in range(1, LOCK_ATTEMPTS + 1):
        try:
            return await repo.get_study_for_update(db, study_id)
        except DBAPIError as error:
            if not isinstance(getattr(error, "orig", None), LockNotAvailableError):
                raise
            await db.rollback()
            if attempt == LOCK_ATTEMPTS:
                raise
            await logger.awarning(
                "process_batch_lock_retry",
                study_id=str(study_id),
                attempt=attempt,
            )
            await asyncio.sleep(LOCK_RETRY_SECONDS)
    return None  # pragma: no cover - el bucle sale por return o por raise


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
        study = await _lock_study(db, requested.study_id)
        if study is None:
            raise RuntimeError("el estudio del lote no existe")

        processed: list[tuple[ECGBatch, int, int]] = []
        pushable: Pushable | None = None
        for pending in await repo.list_batches_to_process(db, study.id):
            failed_batch_id = pending.id
            samples, events, batch_pushable = await _process_one_batch(db, study, pending)
            processed.append((pending, samples, events))
            if batch_pushable is not None and (
                pushable is None or batch_pushable.rank > pushable.rank
            ):
                pushable = batch_pushable

        patient_id = study.patient_id
        await db.commit()
        for done, samples, events in processed:
            await logger.ainfo(
                "process_batch_done",
                batch_id=str(done.id),
                study_id=str(study.id),
                samples=samples,
                events=events,
            )
        # Recién acá, con la transacción cerrada: el `alertId` del push tiene
        # que existir cuando el paciente toque la notificación.
        if pushable is not None:
            await notify_patient_task(
                patient_id,
                anomaly_message(pushable.alert_id, datetime.now(UTC).isoformat(), pushable.kind),
            )
    except DBAPIError as error:
        if not isinstance(getattr(error, "orig", None), LockNotAvailableError):
            await _mark_failed(db, batch_id, failed_batch_id, error)
            return
        # No se pudo tomar la fila ni después de los reintentos. El lote NO es
        # `FAILED`: no tiene nada malo, solo perdió la carrera. Se lo deja
        # pendiente para que lo drene la próxima pasada — marcarlo fallido sería
        # declarar rota una señal que está entera.
        await db.rollback()
        await logger.awarning(
            "process_batch_contended",
            requested_batch_id=str(batch_id),
            pending_batch_id=str(failed_batch_id),
        )
    except Exception as error:  # noqa: BLE001 — el estado del lote tiene que reflejarlo
        await _mark_failed(db, batch_id, failed_batch_id, error)


async def _mark_failed(
    db: AsyncSession, batch_id: uuid.UUID, failed_batch_id: uuid.UUID, error: Exception
) -> None:
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
