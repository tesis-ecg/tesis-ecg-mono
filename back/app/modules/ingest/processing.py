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

**El motor no corre por lote.** Un lote son ~15 s y casi nada de lo clínico
cabe ahí; el motor analiza bloques de la corrida detrás de un cursor, igual que
la vista filtrada (`append_ml_analysis`), y relee crudo y flags de los objetos
que archiva cada lote.
"""

import asyncio
import hashlib
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import structlog
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.s3 import get_object, list_keys, put_object
from app.core.workers import run_cpu, run_io
from app.db.errors import is_lock_contention
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.study import Study, StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment
from app.ml.beats import analyze_window, decode_beats, encode_beats
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
from app.ml.morphology import TemplateBank
from app.ml.pipeline import analyze_batch, build_config
from app.ml.quality import sample_runs
from app.ml.status_flags import has_backlog_overflow, has_corrupt_frame
from app.ml.totals import combine_totals
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest import ml_persistence, timeline
from app.modules.ingest.pushable import PUSH_RANK, Pushable, most_severe
from app.modules.ingest.visual_filter import CONTEXT_SECONDS, filter_visualization
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


def flags_key(study_id: uuid.UUID, first_seq: int) -> str:
    """Flags por muestra del lote (`uint8`), al lado de su segmento.

    El visor no los necesita, pero el motor sí: son la Capa A del gate (lead-off,
    saturación, SQI del firmware) y los picos R del MCU contra los que se mide el
    bSQI. Antes el motor los leía del lote recién decodificado; ahora analiza
    bloques de varios lotes y los relee de acá (`_flags_range`).
    """
    return f"studies/{study_id}/flags/{first_seq:012d}.u8"


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
    entero en cada lote. Esa ruta podía provocar timeouts: `rebuild_pyramid`
    leía de S3 **todas** las envolventes ya
    archivadas del estudio en cada lote (lote 1 leía un objeto, el lote 30 leía
    treinta) mientras tenía tomada la fila del estudio, y el POST siguiente
    moría esperando ese lock a los 15 s del `statement_timeout`.
    """
    return f"studies/{study_id}/levels/{bucket}/{first_seq:012d}.f32"


def level_chunk_prefix(study_id: uuid.UUID, bucket: int) -> str:
    return f"studies/{study_id}/levels/{bucket}/"


def filtered_segment_key(study_id: uuid.UUID, start_sample: int) -> str:
    return f"studies/{study_id}/filtered/segments/{start_sample:012d}.f32"


def filtered_envelope_key(study_id: uuid.UUID, start_sample: int) -> str:
    return f"studies/{study_id}/filtered/envelopes/{start_sample:012d}.f32"


def filtered_level_key(study_id: uuid.UUID, bucket: int) -> str:
    return f"studies/{study_id}/filtered/levels/{bucket}.f32"


def filtered_level_chunk_key(study_id: uuid.UUID, bucket: int, start_sample: int) -> str:
    return f"studies/{study_id}/filtered/levels/{bucket}/{start_sample:012d}.f32"


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


def _read_batch(frames_key: str) -> _DecodedBatch:
    """Las tramas archivadas de un lote, decodificadas. **Bloqueante**: corre en un hilo."""
    return decode_batch(get_object(frames_key))


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


def beat_chunk_key(study_id: uuid.UUID, start_sample: int) -> str:
    return f"studies/{study_id}/beats/{start_sample:012d}.bin"


def beat_compacted_key(study_id: uuid.UUID, end_sample: int) -> str:
    """Latidos fundidos hasta `end_sample`.

    La clave cambia con cada compactación en vez de pisarse: si la transacción
    que la registra se revierte, la metadata vieja sigue apuntando a objetos que
    no cambiaron y ningún latido queda contado dos veces.
    """
    return f"studies/{study_id}/beats/compacted-{end_sample:012d}.bin"


def _object_meta(key: str, payload: bytes, **extra: object) -> dict[str, Any]:
    return {
        "key": key,
        "byteLength": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **extra,
    }


def _chunk_order(study: Study, bucket: int, key: str, *, filtered: bool = False) -> tuple[int, str]:
    """Posición de un chunk dentro de su nivel.

    El objeto compactado va primero: contiene todo lo anterior a los chunks que
    se le anexaron después. El resto ordena por su clave, que lleva la `seq` con
    ceros a la izquierda para que ordenar por texto ordene por tiempo.
    """
    compacted = filtered_level_key(study.id, bucket) if filtered else level_key(study.id, bucket)
    return (0, "") if key == compacted else (1, key)


def _decode_carry(raw: str | None) -> np.ndarray:
    if not raw:
        return np.empty(0, dtype="<f4")
    return np.frombuffer(bytes.fromhex(str(raw)), dtype="<f4")


def append_level_chunks(
    study: Study, base_envelope: np.ndarray, first_seq: int, *, filtered: bool = False
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
    # Copias, nunca los dicts cargados: SQLAlchemy guarda esos mismos objetos
    # como valor original de la columna JSONB. Mutarlos en el lugar dejaba la
    # lista nueva igual a la "original", el flush no emitía el UPDATE y la
    # pirámide se congelaba en el último lote que había agregado un nivel.
    by_bucket: dict[int, dict[str, Any]] = {
        int(level["samplesPerBucket"]): {**level, "chunks": list(level.get("chunks", []))}
        for level in (
            (study.ecg_filtered_pyramid_levels if filtered else study.ecg_pyramid_levels) or []
        )
    }
    carry_state: dict[str, str] = dict(
        (study.ecg_filtered_level_carry if filtered else study.ecg_level_carry) or {}
    )

    for bucket in PYRAMID_BUCKETS:
        if bucket == BASE_BUCKET:
            chunk = base_envelope
            key = (
                filtered_envelope_key(study.id, first_seq)
                if filtered
                else envelope_key(study.id, first_seq)
            )
        else:
            factor = bucket // BASE_BUCKET
            combined = np.concatenate([_decode_carry(carry_state.get(str(bucket))), base_envelope])
            chunk, remainder = reduce_envelope_exact(combined, factor)
            carry_state[str(bucket)] = remainder.tobytes().hex() if remainder.size else ""
            key = (
                filtered_level_chunk_key(study.id, bucket, first_seq)
                if filtered
                else level_chunk_key(study.id, bucket, first_seq)
            )

        if chunk.size == 0:
            continue
        payload = chunk.tobytes()
        if bucket != BASE_BUCKET:
            put_object(key, payload)

        level = by_bucket.get(bucket, {"samplesPerBucket": bucket, "chunks": []})
        chunks = [c for c in level["chunks"] if c.get("key") != key]
        chunks.append(_object_meta(key, payload, pointCount=int(chunk.size)))
        # Ordenados y no en orden de llegada: el cliente concatena los chunks tal
        # como vienen, así que el orden ES la señal. Un lote reprocesado entra por
        # la deduplicación de arriba y se re-anexaría al final, dejando su tramo
        # fuera de lugar dentro del nivel. Es lo mismo que ya hace `ecg_segments`
        # con `startSampleIndex`.
        chunks.sort(
            key=lambda item: _chunk_order(study, bucket, str(item["key"]), filtered=filtered)
        )
        by_bucket[bucket] = {
            **level,
            "chunks": chunks,
            "pointCount": sum(int(c["pointCount"]) for c in chunks),
        }

    if filtered:
        study.ecg_filtered_level_carry = carry_state
    else:
        study.ecg_level_carry = carry_state
    # Un nivel que no comprime no vale los objetos que ocupa en S3.
    total = study.filtered_samples_count if filtered else study.samples_count
    return [level for level in by_bucket.values() if int(level["pointCount"]) < max(total, 1)]


def rebuild_level_metadata(study: Study, *, filtered: bool = False) -> list[dict[str, Any]]:
    """Rearma la metadata de la pirámide a partir de los chunks que hay en S3.

    Repara los estudios que quedaron con la pirámide congelada: la metadata
    dejaba de persistirse, pero cada lote siguió escribiendo sus chunks. Los
    chunks nunca se borran —tampoco al compactar—, así que listarlos alcanza
    para reconstruir el nivel completo. El objeto compactado se ignora: puede
    contener solo el prefijo congelado, y vive fuera del prefijo de chunks.

    Lanza `ValueError` si lo que hay en S3 no cubre exactamente lo procesado.
    """
    processed = study.filtered_samples_count if filtered else study.samples_count
    levels: list[dict[str, Any]] = []
    for bucket in PYRAMID_BUCKETS:
        if bucket == BASE_BUCKET:
            prefix = (
                f"studies/{study.id}/filtered/envelopes/" if filtered else envelope_prefix(study.id)
            )
        else:
            prefix = (
                f"studies/{study.id}/filtered/levels/{bucket}/"
                if filtered
                else level_chunk_prefix(study.id, bucket)
            )
        chunks = []
        for key in list_keys(prefix):
            payload = get_object(key)
            chunks.append(_object_meta(key, payload, pointCount=len(payload) // 4))
        if not chunks:
            continue
        chunks.sort(
            key=lambda item: _chunk_order(study, bucket, str(item["key"]), filtered=filtered)
        )
        point_count = sum(int(chunk["pointCount"]) for chunk in chunks)
        if point_count // 2 != processed // bucket:
            raise ValueError(
                f"El nivel {bucket} en S3 cubre {point_count // 2} buckets y se esperaban "
                f"{processed // bucket}."
            )
        levels.append({"samplesPerBucket": bucket, "pointCount": point_count, "chunks": chunks})
    return [level for level in levels if int(level["pointCount"]) < max(processed, 1)]


def compact_level(study: Study, level: dict[str, Any], *, filtered: bool = False) -> dict[str, Any]:
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
    key = filtered_level_key(study.id, bucket) if filtered else level_key(study.id, bucket)
    put_object(key, payload)
    merged = _object_meta(key, payload, pointCount=len(payload) // 4)
    return {
        "samplesPerBucket": bucket,
        "pointCount": len(payload) // 4,
        "chunks": [merged],
    }


def compact_pyramid(
    study: Study, *, force: bool = False, filtered: bool = False
) -> list[dict[str, Any]]:
    """Compacta los niveles cuyo recuento de chunks cruzó el umbral."""
    return [
        compact_level(study, level, filtered=filtered)
        if force or len(level.get("chunks", [])) >= LEVEL_COMPACTION_THRESHOLD
        else level
        for level in (
            study.ecg_filtered_pyramid_levels if filtered else study.ecg_pyramid_levels or []
        )
    ]


# --------------------------------------------------------------------------- #
# Eventos derivados
# --------------------------------------------------------------------------- #


#: Vive en `app/ml/quality.py`: el gate de calidad hace exactamente el mismo
#: run-length sobre máscaras, y dos copias que se desincronizan producirían
#: bandas distintas para el mismo tramo según quién las calculó.
_runs = sample_runs


@dataclass(frozen=True)
class DerivedEvent:
    kind: str
    event_type: ECGEventType
    severity: ECGEventSeverity
    start_sample: int
    length_samples: int
    alert_message: str | None = None
    #: Campos extra del `metadata` del evento, para lo que no se deduce de las
    #: muestras (por ejemplo la duración real de un hueco, que en el buffer
    #: empaquetado ocupa cero).
    extra_metadata: dict[str, Any] | None = None


#: Umbral para no inundar la base con eventos de un electrodo que rebota. Medio
#: segundo a 500 SPS.
MIN_RUN_SAMPLES = 250

#: Hueco mínimo entre dos tramas para contarlo. `durationMs` viaja en enteros de
#: milisegundo y una muestra son 2 ms: por debajo de esto es redondeo, no señal
#: que falte.
MIN_FRAME_GAP_MS = 20

#: Origen de la Capa A en el `metadata` del evento. Los hallazgos del motor
#: llevan `"source": "ml"`; sin esta marca, un tramo que rechazan los bits del
#: hardware y uno que rechaza el gate del motor serían indistinguibles en la base.
FIRMWARE_SOURCE = "firmware_flags"


def derive_events(
    batch: _DecodedBatch, sample_rate: int, *, previous_end_ms: int | None = None
) -> list[DerivedEvent]:
    """Lo que el médico va a mirar, que no son las 43 M de muestras.

    Las reglas de interpretación son las de `INTEGRACION.md` §4.5:
    `LEAD_OFF` invalida el tramo (pero las muestras **se conservan**, marcadas),
    `RLD_OFF` no invalida nada, y con SQI = 1 no se cuentan latidos.

    `previous_end_ms` es el `t0Ms` en que terminó el lote anterior cuando éste
    continúa su tramo (`_place_on_timeline`). Con él, el hueco entre lotes se
    mide igual que el hueco entre dos tramas del mismo lote: un lote de ~15 s
    tiene ~48 tramas, así que de otro modo uno de cada 48 bordes quedaba sin
    mirar.
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
    # faltara ninguna muestra. Un hueco no es una línea isoeléctrica, pero el
    # exceso chico sí es reloj: el ADS1292R muestrea con su propio oscilador y
    # `durationMs` es millis(), así que solo cuenta lo que excede esa tolerancia.
    offset = 0
    for frame in batch.frames:
        # Hueco ENTRE tramas. El `gap_beyond_clock_ms` de abajo solo ve lo que
        # falta *adentro* de una trama; lo que el equipo dejó de adquirir entre el
        # final de una y el arranque de la siguiente no lo miraba nadie. Las
        # tramas de un lote son contiguas por `seq` y de un solo `bootId` (el
        # filtro está en `ingest_service`), así que un salto de `t0Ms` acá es
        # adquisición perdida de verdad, no un corte de enlace: si se cae el
        # enlace el equipo sigue grabando en su flash y el lote llega tarde pero
        # entero. Importa el doble con un modelo de detección atrás — un tramo
        # isoeléctrico sintético es indistinguible de una asistolia.
        if previous_end_ms is not None:
            between_ms = frame.info.t0_ms - previous_end_ms
            if between_ms >= MIN_FRAME_GAP_MS:
                events.append(
                    DerivedEvent(
                        kind="frame_gap",
                        event_type=ECGEventType.OTHER,
                        severity=ECGEventSeverity.MEDIUM,
                        start_sample=offset,
                        length_samples=int(between_ms * sample_rate / 1000),
                    )
                )
        previous_end_ms = frame.info.t0_ms + frame.info.duration_ms

        gap_ms = frame.info.gap_beyond_clock_ms
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

    # Todo lo de acá es Capa A: sale de los bits y de la cabecera que escribe el
    # firmware. La Capa A **es** la primera capa del gate de calidad del motor,
    # pero la escribe este camino y no `ml_persistence`, y la marca es lo que
    # permite separarlas después.
    return [
        replace(event, extra_metadata={**(event.extra_metadata or {}), "source": FIRMWARE_SOURCE})
        for event in events
    ]


# `Pushable` y la tabla de severidades que notifican viven en
# `ingest/pushable.py`: las comparte `ml_persistence`, que no puede importar este
# módulo. Se re-exportan acá para los importadores de `processing.Pushable`.


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
            # `recount_events` cuenta por esta columna: sin ella los eventos de la
            # Capa A y de pérdida de señal no entrarían en `events_count`.
            study_id=study.id,
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
                **(derived.extra_metadata or {}),
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
            rank = PUSH_RANK.get(derived.severity)
            if rank is not None:
                pushable = most_severe(
                    pushable, Pushable(rank=rank, alert_id=alert.id, kind=derived.kind)
                )
    return len(events), pushable


def signal_loss_events(batch: ECGBatch, gap_ms: int) -> list[DerivedEvent]:
    """Lo que el equipo perdió y no está en ninguna muestra.

    Los eventos de `derive_events` salen de los flags por muestra, o sea de
    señal que SÍ llegó. Éstos son lo contrario: registro que no existe en ningún
    lado y que solo se conoce por el `seq` que falta y por los flags del paquete
    de STATUS (`INTEGRACION.md` §4.6 y §11.1).

    **Ocupan cero muestras**, y eso es deliberado. El buffer del estudio es
    continuo por construcción: un hueco no mete muestras, abre un tramo nuevo en
    la línea de tiempo. Con `sampleCount = 0` la anotación queda como una marca
    en el punto exacto de la discontinuidad en vez de una banda que taparía
    señal real (`studies.annotations.event_offsets_ms` deriva el final del rango
    de ese campo). La duración de verdad viaja en `gapMs`.

    Son idempotentes por construcción: todo sale de columnas del lote, así que
    reprocesarlo da exactamente los mismos eventos.
    """
    events: list[DerivedEvent] = []

    if batch.preceding_seq_gap_frames > 0:
        # El bit 0 del STATUS es la confirmación del propio equipo de que pisó
        # backlog sin confirmar. Sin él el hueco igual es real —las tramas no
        # llegaron— pero la causa queda inferida.
        confirmed = has_backlog_overflow(batch.device_status_flags)
        events.append(
            DerivedEvent(
                kind="backlog_overflow" if confirmed else "missing_frames_inferred",
                event_type=ECGEventType.OTHER,
                severity=ECGEventSeverity.HIGH,
                start_sample=0,
                length_samples=0,
                alert_message=(
                    "El Holter confirmó que sobreescribió señal sin subir. "
                    if confirmed
                    else "Faltan tramas del registro; la causa todavía no está confirmada. "
                )
                + f"Duración estimada: {_human_duration(gap_ms)}.",
                extra_metadata={
                    "gapFrames": batch.preceding_seq_gap_frames,
                    "gapMs": gap_ms,
                    "cause": "device_confirmed" if confirmed else "inferred",
                },
            )
        )

    if has_corrupt_frame(batch.device_status_flags):
        events.append(
            DerivedEvent(
                kind="corrupt_frame",
                event_type=ECGEventType.OTHER,
                severity=ECGEventSeverity.MEDIUM,
                start_sample=0,
                length_samples=0,
                extra_metadata={"cause": "device_confirmed"},
            )
        )

    return events


def _human_duration(ms: int) -> str:
    """Duración en castellano, para un mensaje que lee un médico."""
    if ms <= 0:
        return "no determinada"
    if ms < 60_000:
        return f"{max(1, round(ms / 1000))} s"
    minutes = ms // 60_000
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d} min"


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #


async def _place_on_timeline(
    db: AsyncSession,
    study: Study,
    batch: ECGBatch,
    decoded: _DecodedBatch,
    start_sample_index: int,
) -> tuple[int, int | None]:
    """Abre o extiende el tramo al que pertenece este lote.

    `start_sample_index` es la posición del lote dentro del buffer empaquetado
    del estudio, que es lo que después permite traducir índice de muestra a hora
    de pared y al revés.

    Devuelve `(gap_ms, previous_end_t0_ms)`:

    - **cuántos milisegundos de hueco** quedaron antes de este lote (0 si se pegó
      al tramo anterior);
    - si se pegó, el `t0Ms` en que terminó la última trama del lote anterior. Un
      salto chico (por debajo de la tolerancia que abre tramo) entre ese final y
      la primera trama de este lote es adquisición perdida igual que uno entre
      dos tramas del mismo lote, y `derive_events` lo marca como `frame_gap`.

    Sale de acá y no de una función aparte porque es el único punto del
    procesamiento que tiene el tramo previo a la vista.
    """
    first = decoded.frames[0].info
    last = decoded.frames[-1].info
    timing = timeline.batch_timing(batch, first.t0_ms, last.t0_ms, last.duration_ms)

    current = await repo.get_last_timeline_segment(db, study.id)
    if current is None or timeline.starts_new_segment(current, batch, timing):
        ordinal = 0 if current is None else current.ordinal + 1
        gap_ms = timeline.gap_before_ms(current, batch, timing)
        await repo.add_timeline_segment(
            db,
            timeline.open_segment(
                study, batch, timing, ordinal, start_sample_index, decoded.n_samples
            ),
        )
        return gap_ms, None

    # Antes de extender: `extend_segment` corre `end_epoch_ms` al final de este
    # lote. Mismo cálculo que `timeline.starts_new_segment` (mismo `bootId`, en
    # el reloj del equipo); sin `t0Ms` crudo, el tramo del backfill viejo no da
    # una referencia exacta y no se compara.
    previous_end_t0_ms = (
        timeline.t0_at(current, current.end_epoch_ms) if current.last_t0_ms is not None else None
    )

    # `list_boot_anchors` ya filtra las filas sin ancla completa, así que las dos
    # columnas están; el `or 0` es solo para el tipo.
    anchors = [
        (int(row.device_uptime_ms or 0), int(row.bridge_epoch_ms or 0))
        for row in await repo.list_boot_anchors(db, study.id, batch.boot_id, current.first_seq)
    ]
    timeline.extend_segment(current, batch, timing, decoded.n_samples, anchors)
    if (
        current.ordinal == 0
        and current.anchor_matches_boot is True
        and study.status is StudyStatus.IN_PROGRESS
        and current.start_epoch_ms <= int(batch.received_at.timestamp() * 1000)
    ):
        study.started_at = datetime.fromtimestamp(current.start_epoch_ms / 1000, tz=UTC)
        study.started_at_verified = True
    return 0, previous_end_t0_ms


async def _raw_signal_range(study: Study, start: int, end: int) -> np.ndarray:
    """Read a bounded contiguous range from immutable decoded raw segments.

    Shared by the filtered view and the beat pass. The GETs run in a thread
    (`run_io`): on the event loop they froze every other request of the same
    instance while a batch was being processed.
    """
    return await run_io(_raw_range, list(study.ecg_segments), start, end)


def _raw_range(segments: list[dict[str, Any]], start: int, end: int) -> np.ndarray:
    """`_raw_signal_range` sobre una copia de `study.ecg_segments`.

    Recibe la lista y no el `Study` para poder correr en un hilo (`run_io`): los
    GET de S3 de un bloque son decenas, y en el event loop lo congelaban con la
    fila del estudio tomada. Tocar ahí un atributo de una entidad expirada
    dispararía un refresh lazy de SQLAlchemy fuera del greenlet.
    """
    parts: list[np.ndarray] = []
    cursor = start
    for segment in segments:
        segment_start = int(segment["startSampleIndex"])
        segment_end = segment_start + int(segment["sampleCount"])
        if segment_end <= cursor or segment_start >= end:
            continue
        if segment_start > cursor:
            raise RuntimeError("Hay un hueco entre los segmentos crudos del estudio.")
        payload = get_object(str(segment["key"]))
        raw = np.frombuffer(payload, dtype="<f4")
        if raw.size != segment_end - segment_start:
            raise RuntimeError("Segmento crudo incompleto.")
        stop = min(end, segment_end)
        parts.append(raw[cursor - segment_start : stop - segment_start])
        cursor = stop
        if cursor == end:
            break
    if cursor != end:
        raise RuntimeError("Faltan muestras crudas para el rango pedido.")
    return np.concatenate(parts) if parts else np.empty(0, dtype="<f4")


def _flags_range(
    study_id: uuid.UUID, segments: list[dict[str, Any]], start: int, end: int
) -> np.ndarray:
    """Los flags por muestra del mismo rango, gemelo de `_raw_range`.

    Cada segmento marca con `firstSeq` que su lote archivó los flags
    (`flags_key`, derivable de esa marca: guardar la clave entera agregaba ~130
    bytes por lote a un JSONB que se reescribe en cada lote). Un segmento sin la
    marca —ingerido antes de que cada lote archivara sus flags— aporta
    **ceros**. Cero no es un valor inventado: es lo que mandaría un equipo sin
    bits de estado. Sin veto de la Capa A (lead-off, saturación, SQI del
    firmware) el gate decide con los índices de la señal sola, y sin picos R del
    firmware no hay bSQI. Para un estudio viejo es la única lectura honesta.
    """
    parts: list[np.ndarray] = []
    cursor = start
    for segment in segments:
        segment_start = int(segment["startSampleIndex"])
        segment_end = segment_start + int(segment["sampleCount"])
        if segment_end <= cursor or segment_start >= end:
            continue
        if segment_start > cursor:
            raise RuntimeError("Hay un hueco entre los segmentos crudos del estudio.")
        stop = min(end, segment_end)
        first_seq = segment.get("firstSeq")
        if first_seq is None:
            parts.append(np.zeros(stop - cursor, dtype=np.uint8))
        else:
            flags = np.frombuffer(get_object(flags_key(study_id, int(first_seq))), dtype=np.uint8)
            if flags.size != segment_end - segment_start:
                raise RuntimeError("Flags incompletos para un segmento crudo.")
            parts.append(flags[cursor - segment_start : stop - segment_start])
        cursor = stop
        if cursor == end:
            break
    if cursor != end:
        raise RuntimeError("Faltan flags para el rango pedido.")
    return np.concatenate(parts) if parts else np.empty(0, dtype=np.uint8)


def _flags_known_range(segments: list[dict[str, Any]], start: int, end: int) -> np.ndarray:
    """Por muestra del rango: verdadera si sus flags están archivados (`firstSeq`).

    Los ceros con que `_flags_range` rellena un segmento viejo son una lectura
    honesta para el veto de la Capa A, pero no para el bSQI: "el firmware no
    marcó ningún R" no es lo mismo que "no sabemos qué marcó". Un bloque que
    cruza el despliegue tiene de las dos, y decidir el bSQI con los picos del
    bloque entero volvía MARGINAL el ECG limpio de la parte sin flags. Con esta
    máscara el gate lo decide ventana por ventana (`quality.assess_quality`).
    Sale de la metadata, sin tocar S3.
    """
    known = np.zeros(max(end - start, 0), dtype=bool)
    for segment in segments:
        segment_start = int(segment["startSampleIndex"])
        segment_end = segment_start + int(segment["sampleCount"])
        if segment_end <= start or segment_start >= end or segment.get("firstSeq") is None:
            continue
        known[max(segment_start, start) - start : min(segment_end, end) - start] = True
    return known


async def append_filtered_view(db: AsyncSession, study: Study) -> None:
    """Commit the safe prefix of every continuous run, with symmetric context."""
    if not study.filter_view_enabled:
        return
    segments = await repo.list_timeline_segments(db, study.id)
    if not segments:
        return
    rate = study.sample_rate or 500
    context = CONTEXT_SECONDS * rate
    cursor = study.filtered_samples_count
    for index, run in enumerate(segments):
        run_start = run.start_sample_index
        run_end = run_start + run.sample_count
        if cursor >= run_end:
            continue
        if cursor < run_start:
            raise RuntimeError("La vista filtrada dejó muestras sin procesar entre tramos.")
        is_active_run = index == len(segments) - 1 and study.status is StudyStatus.IN_PROGRESS
        safe_end = max(run_start, run_end - context) if is_active_run else run_end
        if safe_end <= cursor:
            break
        read_start = max(run_start, cursor - context)
        read_end = min(run_end, safe_end + context)
        raw = await _raw_signal_range(study, read_start, read_end)
        filtered = filter_visualization(raw, rate)[cursor - read_start : safe_end - read_start]
        if filtered.size != safe_end - cursor:
            raise RuntimeError("La vista filtrada no cubre el tramo esperado.")
        key = filtered_segment_key(study.id, cursor)
        payload = filtered.tobytes()
        await run_io(put_object, key, payload)
        filtered_segments = [item for item in study.ecg_filtered_segments if item.get("key") != key]
        filtered_segments.append(
            _object_meta(key, payload, startSampleIndex=cursor, sampleCount=int(filtered.size))
        )
        filtered_segments.sort(key=lambda item: int(item["startSampleIndex"]))
        study.ecg_filtered_segments = filtered_segments
        old_carry = (
            np.frombuffer(study.ecg_filtered_envelope_carry, dtype="<f4")
            if study.ecg_filtered_envelope_carry
            else np.empty(0, dtype="<f4")
        )
        envelope, remainder = build_envelope(np.concatenate((old_carry, filtered)))
        if envelope.size:
            await run_io(put_object, filtered_envelope_key(study.id, cursor), envelope.tobytes())
        study.ecg_filtered_envelope_carry = remainder.tobytes() if remainder.size else None
        study.filtered_samples_count = safe_end
        study.ecg_filtered_pyramid_levels = append_level_chunks(
            study, envelope, cursor, filtered=True
        )
        study.ecg_filtered_pyramid_levels = compact_pyramid(study, filtered=True)
        # Red de seguridad: las columnas JSONB no rastrean mutaciones internas.
        flag_modified(study, "ecg_filtered_segments")
        flag_modified(study, "ecg_filtered_pyramid_levels")
        cursor = safe_end


#: Señal a cada lado del tramo que se analiza. Alcanza para que el umbral
#: adaptativo del detector arranque aprendido y para que el pasa-altos de
#: 0,05 Hz del ST no deje transitorio dentro del tramo conservado.
BEAT_CONTEXT_SECONDS = 30
#: Tope por pasada durante la ingesta. Un estudio anterior a esta función tiene
#: todo su historial sin analizar; recorrerlo de una dentro del procesamiento de
#: un lote retendría la fila del estudio el tiempo que el chaleco no puede
#: esperar. Se pone al día de a dos horas por lote, o con el backfill.
BEAT_SAMPLES_PER_PASS = 2 * 3600 * 500


def _analysis_runs(study: Study, segments: list[Any]) -> list[tuple[int, int]]:
    if segments:
        return [(segment.start_sample_index, segment.sample_count) for segment in segments]
    return [(0, study.samples_count)] if study.ecg_segments else []


async def append_beat_analysis(
    db: AsyncSession, study: Study, *, max_samples: int | None = BEAT_SAMPLES_PER_PASS
) -> None:
    """Detecta latidos en lo que falta analizar, tramo por tramo de la línea de tiempo.

    Mismo esquema que `append_filtered_view`: el cursor avanza solo sobre señal
    que ya tiene contexto a los dos lados, y la cola del tramo activo espera al
    lote siguiente. Nada cruza un corte de la línea de tiempo, y la señal que
    ninguna corrida cubre se saltea. Los que la llaman la corren en `_guarded`.
    """
    runs = _analysis_runs(study, await repo.list_timeline_segments(db, study.id))
    if not runs:
        return
    rate = study.sample_rate or 500
    context = BEAT_CONTEXT_SECONDS * rate
    cursor = study.beats_analyzed_samples
    budget = max_samples
    chunks = list(study.ecg_beat_chunks or [])
    for index, (run_start, run_count) in enumerate(runs):
        run_end = run_start + run_count
        if cursor >= run_end:
            continue
        if cursor < run_start:
            # Señal que ninguna corrida cubre: un estudio que ya tenía muestras
            # antes de la línea de tiempo y al que no se le corrió
            # `backfill_timeline` (su primera corrida arranca en el
            # `samples_count` que tenía). No se sabe dónde están sus huecos, así
            # que no se puede analizar por corrida: se saltea hasta el inicio de
            # la corrida, igual que el motor (`_pending_blocks`). Antes era un
            # `RuntimeError` que dejaba `FAILED` cada lote siguiente del estudio;
            # después, un cursor clavado que dejaba el informe en
            # `BEAT_ANALYSIS_PENDING` para siempre. Saltearla no baja
            # `averageBpm`: el tiempo analizado de `compute_holter_metrics` se
            # suma solo sobre las corridas, y esta señal no está en ninguna.
            # Si después se le corre `backfill_timeline`, esa señal pasa a
            # tener corrida y el cursor ya la dejó atrás: el script vuelve los
            # latidos del estudio a cero (`beats_analyzed_samples`,
            # `ecg_beat_chunks`) para que se analice.
            logger.warning(
                "beat_signal_without_run_skipped",
                study_id=str(study.id),
                start_sample=cursor,
                end_sample=run_start,
            )
            cursor = run_start
        is_active_run = index == len(runs) - 1 and study.status is StudyStatus.IN_PROGRESS
        safe_end = max(run_start, run_end - context) if is_active_run else run_end
        if budget is not None:
            safe_end = min(safe_end, cursor + budget)
        if safe_end <= cursor:
            break
        read_start = max(run_start, cursor - context)
        read_end = min(run_end, safe_end + context)
        raw = await _raw_signal_range(study, read_start, read_end)
        beats = analyze_window(raw, rate, offset=read_start, keep_start=cursor, keep_end=safe_end)
        if beats.size:
            key = beat_chunk_key(study.id, cursor)
            payload = encode_beats(beats)
            await run_io(put_object, key, payload)
            chunks = [chunk for chunk in chunks if chunk.get("key") != key]
            chunks.append(
                _object_meta(
                    key,
                    payload,
                    startSampleIndex=cursor,
                    sampleCount=safe_end - cursor,
                    beatCount=int(beats.size),
                )
            )
        if budget is not None:
            budget -= safe_end - cursor
        cursor = safe_end
        if safe_end < run_end:
            break
    chunks.sort(key=lambda item: int(item["startSampleIndex"]))
    study.ecg_beat_chunks = chunks
    study.beats_analyzed_samples = cursor
    study.ecg_beat_chunks = compact_beat_chunks(study)
    flag_modified(study, "ecg_beat_chunks")


def compact_beat_chunks(study: Study, *, force: bool = False) -> list[dict[str, Any]]:
    """Funde los chunks de latidos en uno, al cruzar el umbral o al cerrar."""
    chunks = list(study.ecg_beat_chunks or [])
    if len(chunks) <= 1 or (not force and len(chunks) < LEVEL_COMPACTION_THRESHOLD):
        return chunks
    payload = b"".join(get_object(str(chunk["key"])) for chunk in chunks)
    start = int(chunks[0]["startSampleIndex"])
    end = int(chunks[-1]["startSampleIndex"]) + int(chunks[-1]["sampleCount"])
    key = beat_compacted_key(study.id, end)
    put_object(key, payload)
    return [
        _object_meta(
            key,
            payload,
            startSampleIndex=start,
            sampleCount=end - start,
            beatCount=sum(int(chunk["beatCount"]) for chunk in chunks),
        )
    ]


def load_beats(study: Study) -> np.ndarray:
    """Todos los latidos analizados del estudio, en orden de muestra."""
    parts = [decode_beats(get_object(str(chunk["key"]))) for chunk in study.ecg_beat_chunks or []]
    if not parts:
        return decode_beats(b"")
    return np.concatenate(parts)


# --------------------------------------------------------------------------- #
# Motor de detección por bloques
# --------------------------------------------------------------------------- #


#: Tiempo de una pasada del motor con la fila del estudio tomada, además del
#: tope de bloques (`ml_analysis_max_blocks_per_pass`). La ingesta del lote
#: siguiente espera la fila con un `lock_timeout` de 3 s y, si no la consigue,
#: el equipo recibe un 503. Un bloque en régimen tarda ~1 s (los ~50 GET de S3
#: más el análisis) y con S3 lento bastante más. Por eso el corte es
#: **predictivo**: antes de cada bloque se suma lo que tardó el anterior, y si
#: el total pasaría este tiempo la pasada termina ahí y deja el resto para la
#: siguiente, como con el tope. Mirar solo lo transcurrido dejaba arrancar un
#: bloque a los 1,9 s, que terminaba a los 3. El primer bloque corre siempre:
#: sin eso, con S3 lento el motor no avanzaría nunca. Va además una sola
#: pasada por transacción (`process_batch` la corre después de drenar los
#: lotes, no una por lote).
ML_PASS_BUDGET_SECONDS = 1.5


@dataclass(frozen=True)
class MlPass:
    """Lo que dejó una pasada de `append_ml_analysis`."""

    #: Filas escritas, actualizadas o empalmadas, para el log.
    written: int = 0
    #: La alerta más severa a notificar después del commit.
    pushable: Pushable | None = None
    #: Quedó señal lista para analizar que esta pasada no alcanzó a cubrir
    #: (`ml_analysis_max_blocks_per_pass`, `ML_PASS_BUDGET_SECONDS`).
    pending: bool = False


def _pending_blocks(
    runs: list[StudyTimelineSegment],
    cursor: int,
    block: int,
    *,
    flush_tail: bool,
    lookahead: int = 0,
) -> Iterator[tuple[StudyTimelineSegment, int, int, int]]:
    """`(corrida, inicio, fin, fin de lectura)` de cada bloque listo desde el cursor.

    Mismo recorrido que `append_filtered_view`: las corridas tilean el buffer
    empaquetado. Un bloque nunca cruza el final de su corrida: entre dos
    corridas hay un hueco real de grabación, y un R-R que lo atravesara mediría
    el corte y no el corazón.

    Señal que **ninguna corrida cubre** —un estudio que ya tenía muestras antes
    de que existiera la línea de tiempo, y al que no se le corrió
    `backfill_timeline`: su primera corrida arranca en `samples_count`— no se
    puede analizar por corrida, porque no se sabe dónde están sus huecos. Se
    salta hasta el inicio de la corrida siguiente. Antes era un `RuntimeError`,
    y como el cursor no se movía, todos los lotes siguientes del estudio
    fallaban en el mismo punto: sin Capa A, sin visor, sin vista filtrada.

    La cola de una corrida (lo que no llega a un bloque entero) espera a que la
    corrida se cierre —que se abra otra después— o a `flush_tail`, que cierra
    también la última: el estudio ya no está en curso, o la corrida lleva
    demasiado sin crecer (`flush_stale_tails`). Mientras esté abierta, el lote
    siguiente la puede completar.

    Cada bloque se lee hasta `lookahead` muestras después de su fin (contexto
    derecho, `pipeline.analyze_batch`), sin pasar el final de la corrida. En la
    corrida abierta, un bloque entero espera además a que la corrida lo pase
    por ese tanto: analizado antes, su final volvería a ser un borde duro y lo
    que decide ahí ya no se corrige. En una corrida cerrada se lee lo que haya,
    que es lo mismo que ve el análisis de corrido: después no hay señal.
    """
    for index, run in enumerate(runs):
        run_start = run.start_sample_index
        run_end = run_start + run.sample_count
        if cursor >= run_end:
            continue
        cursor = max(cursor, run_start)
        run_closed = flush_tail or index < len(runs) - 1
        while cursor < run_end:
            end = min(cursor + block, run_end)
            read_end = min(end + lookahead, run_end)
            if not run_closed and (end - cursor < block or read_end - end < lookahead):
                return
            yield run, cursor, end, read_end
            cursor = end


def _segment_first_seq(study: Study, sample: int) -> int | None:
    for segment in study.ecg_segments:
        start = int(segment["startSampleIndex"])
        if start <= sample < start + int(segment["sampleCount"]):
            first_seq = segment.get("firstSeq")
            return int(first_seq) if first_seq is not None else None
    return None


async def _attribution_batch(
    db: AsyncSession, study: Study, batch: ECGBatch | None, block_end: int
) -> uuid.UUID:
    """El lote al que se atribuye lo que escribe un bloque. Nunca NULL.

    El lote que disparó el análisis, si lo hay. En la finalización no hay: se
    usa el que archivó la última muestra del bloque (el `firstSeq` de su
    segmento) y, para segmentos de antes de que existiera esa marca, el último
    lote del estudio. Las dos reglas son deterministas, así que repetir la
    finalización atribuye igual.
    """
    if batch is not None:
        return batch.id
    first_seq = _segment_first_seq(study, block_end - 1)
    if first_seq is not None:
        covering = await repo.get_batch_id_by_first_seq(db, study.id, first_seq)
        if covering is not None:
            return covering
    latest = await repo.get_latest_batch_id(db, study.id)
    if latest is None:
        raise RuntimeError("El estudio tiene señal pero ningún lote al que atribuirla.")
    return latest


def _block_signal(
    study_id: uuid.UUID, segments: list[dict[str, Any]], start: int, end: int
) -> tuple[np.ndarray, np.ndarray]:
    """Crudo y flags de un bloque. **Bloqueante**: corre en un hilo (`run_io`)."""
    return _raw_range(segments, start, end), _flags_range(study_id, segments, start, end)


def _push_from_sample(run: StudyTimelineSegment, rate: int, now: datetime) -> int:
    """Primera muestra de la corrida cuyo hallazgo todavía merece un push.

    La que cae `ml_push_max_age_minutes` antes de `now` en hora de pared, por
    la línea de tiempo de la corrida. Antes del inicio de la corrida es su
    inicio (todo es reciente); después de su final, ninguna muestra llega y no
    avisa nada. El motor analiza de todo menos señal recién llegada: la cola de
    una corrida que dejó de crecer, el backlog de horas que el chaleco sube al
    volver a la casa, los bloques atrasados de cuando el motor estuvo apagado.
    Todos se escriben y alertan al médico; solo lo reciente despierta al
    paciente.

    La hora se pasa a muestras con la frecuencia **medida** de la corrida —su
    duración de pared sobre sus muestras, la misma cuenta que hace
    `annotations.segment_epoch_ms`— y no con los 500 Hz nominales. El
    ADS1292R de esta placa corre ~0,25 % lento y hasta ±1,5 % con la
    temperatura (`INTEGRACION.md` §4.4): a 498,7 Hz, contar a 500 corre el
    corte ~9 s por hora de corrida, y en una corrida de diez días la ventana
    de una hora quedaba en veinte minutos —una pausa crítica de hace quince ya
    no avisaba— o, a -1,5 %, el corte caía en el futuro y no avisaba nada.

    Una corrida sin hora propia no avisa: el backlog de un arranque anterior
    del equipo (`anchor_matches_boot` en falso) se ancla a la hora en que
    **llegó**, no a la que se grabó (`ingest_service`), así que horas de
    backlog parecían recién grabadas y cada pausa vieja despertaba al
    paciente. Su alerta queda para el médico.
    """
    minutes = settings.ml_push_max_age_minutes
    if minutes <= 0:
        return 0
    if run.anchor_matches_boot is False:
        return run.start_sample_index + run.sample_count + 1
    span_ms = run.end_epoch_ms - run.start_epoch_ms
    samples_per_ms = (
        run.sample_count / span_ms if span_ms > 0 and run.sample_count > 0 else rate / 1000
    )
    cutoff_ms = now.timestamp() * 1000 - minutes * 60_000
    offset = math.ceil((cutoff_ms - run.start_epoch_ms) * samples_per_ms)
    return run.start_sample_index + max(offset, 0)


def _known_or_none(known: np.ndarray) -> np.ndarray | None:
    """`None` si todos los flags del bloque están archivados, el caso de régimen."""
    return None if bool(known.all()) else known


async def append_ml_analysis(
    db: AsyncSession, study: Study, batch: ECGBatch | None, *, flush_tail: bool | None = None
) -> MlPass:
    """Analiza los bloques que el cursor todavía no cubrió. Mismo patrón que la vista filtrada.

    **Por qué bloques y no lotes.** Un lote son ~15 s (48 tramas del puente) y una
    taquicardia tiene que sostenerse 30 s (`ml_rhythm_min_seconds`): por lote,
    el motor no podía ver ninguna, perdía cada pausa y cada R-R que cruzaba un
    POST y reiniciaba la referencia de prematuridad cada quince latidos. Acá
    cada corrida de la línea de tiempo se recorre en bloques de
    `ml_analysis_block_seconds`, cada uno con `ml_analysis_context_seconds` de
    contexto izquierdo y `ml_analysis_lookahead_seconds` de contexto derecho de
    la misma corrida (`pipeline.analyze_batch` sabe qué no informar de ahí).
    `study.ml_analyzed_samples` es el cursor: cada
    muestra cae en la parte nueva de un solo bloque, y eso es lo que hace que
    los totales del estudio (`app/ml/totals.py`) se sumen exactos.

    Por bloque: crudo y flags del rango (leídos en un hilo, `_block_signal`),
    los empalmes de la corrida (`repo.SPLICE_KINDS`, ya persistidos, que el
    motor saca de lo analizable) y el banco que viene del bloque anterior. El
    banco se carga una vez por pasada, se enhebra de bloque en bloque y se
    guarda una vez al final, con clave versionada por el final del último
    bloque.

    `batch` es el lote que disparó la pasada, al que se atribuyen las filas; en
    la finalización (`process_study_task`) es `None` y se atribuye al lote que
    cubre el final de cada bloque (`_attribution_batch`). Las alertas se
    escriben siempre; el push al paciente, solo por lo que terminó hace menos
    de `ml_push_max_age_minutes` (`_push_from_sample`).

    `flush_tail` decide si la cola de la **última** corrida se analiza aunque no
    llegue a un bloque entero. Por omisión, solo sin lote y con el estudio
    cerrado. Desde un lote nunca, aunque el estudio ya esté cerrado: un estudio
    se cierra con lotes en cola, y si cada lote drenado después del cierre
    analizara su propia "cola", el análisis volvería a ser por lote —15 s con
    60 s de contexto— y dependería del tamaño de los lotes. La cola la analiza
    una sola vez `process_study_task`, que corre después de drenarlos.

    Con `ml_enabled` en falso no hace nada y **no mueve el cursor**: al volver a
    prenderlo, el motor retoma desde donde quedó y se pone al día de a
    `ml_analysis_max_blocks_per_pass` bloques por lote, con la Capa A intacta
    mientras tanto (`_persist_events` no depende de esto).
    """
    if not settings.ml_enabled:
        return MlPass()
    if flush_tail is None:
        flush_tail = batch is None and study.status is not StudyStatus.IN_PROGRESS
    runs = await repo.list_timeline_segments(db, study.id)
    rate = study.sample_rate or 500
    block = max(round(settings.ml_analysis_block_seconds * rate), 1)
    context = round(settings.ml_analysis_context_seconds * rate)
    lookahead = round(settings.ml_analysis_lookahead_seconds * rate)
    budget = settings.ml_analysis_max_blocks_per_pass
    state: dict[str, Any] = study.ml_state or {}
    score_floor = float(state.get("scoreFloor", 0.0))
    totals: dict[str, float] = dict(state.get("totals") or {})
    bank: TemplateBank | None = None
    splices: list[tuple[int, int, str]] = []
    segments: list[dict[str, Any]] = []
    written, analyzed, last_end = 0, 0, study.ml_analyzed_samples
    pushable: Pushable | None = None
    now = datetime.now(UTC)
    started = time.monotonic()
    last_block_seconds = 0.0

    for run, block_start, block_end, read_end in _pending_blocks(
        runs, study.ml_analyzed_samples, block, flush_tail=flush_tail, lookahead=lookahead
    ):
        block_started = time.monotonic()
        if analyzed == budget or (
            analyzed and block_started - started + last_block_seconds > ML_PASS_BUDGET_SECONDS
        ):
            return _finish_ml_pass(study, bank, last_end, totals, written, pushable, True)
        if block_start > last_end:
            _log_skipped(study, last_end, block_start)
        read_start = max(run.start_sample_index, block_start - context)
        if bank is None:
            # Una sola vez por pasada, y solo si hay algo que analizar: la mayoría
            # de los lotes de una corrida abierta no completan un bloque.
            bank = ml_persistence.load_bank(state, build_config(settings, rate))
            splices = await repo.list_splices(db, study.id, read_start, rate)
            segments = list(study.ecg_segments)
        config = build_config(settings, rate, score_floor=score_floor)
        existing = await ml_persistence.count_anomalies(db, study.id)
        raw, flags = await run_io(_block_signal, study.id, segments, read_start, read_end)
        # LA frontera. Todo el análisis corre acá adentro, fuera del event loop,
        # en una sola llamada auditable. Del otro lado no cruza nada del ORM:
        # arrays, el banco (un dataclass), la config y los empalmes.
        result = await run_cpu(
            analyze_batch,
            raw,
            flags,
            start_sample_index=read_start,
            bank=bank,
            config=config,
            # Única por bloque y estable entre reintentos: un bloque que se
            # vuelve a correr sobre el banco ya guardado no se pliega dos veces.
            fold_key=f"{block_start}:{block_end}",
            existing_anomalies=existing,
            context_samples=block_start - read_start,
            lookahead_samples=read_end - block_end,
            flags_known=_known_or_none(_flags_known_range(segments, read_start, read_end)),
            # El motor ubica cada empalme por su inicio; el largo es tiempo que
            # falta, no muestras del buffer.
            gap_samples=tuple(
                (start - read_start, count)
                for start, count, kind in splices
                if read_start <= start < read_end
                and not (kind in repo.BOUNDARY_SPLICE_KINDS and start == run.start_sample_index)
            ),
        )
        scope = ml_persistence.BlockScope(
            batch_id=await _attribution_batch(db, study, batch, block_end),
            boot_id=run.boot_id,
            run_start=run.start_sample_index,
            read_start=read_start,
            block_start=block_start,
            block_end=block_end,
            push_from=_push_from_sample(run, rate, now),
        )
        count, block_pushable = await ml_persistence.persist_analysis(
            db, study, result, rate, scope
        )
        written += count
        pushable = most_severe(pushable, block_pushable)
        bank = result.bank
        score_floor = bank.score_floor
        totals = combine_totals(totals, result.totals)
        study.ml_analyzed_samples = last_end = block_end
        analyzed += 1
        last_block_seconds = time.monotonic() - block_started

    if flush_tail and last_end < study.samples_count:
        # Lo que queda detrás de la última corrida en un estudio cerrado —o el
        # estudio entero, si nunca tuvo línea de tiempo (los seeds, uno viejo
        # sin `backfill_timeline`)— no se va a poder analizar nunca. Sin mover
        # el cursor, la recuperación del manifest lo volvería a intentar en
        # cada vista.
        _log_skipped(study, last_end, study.samples_count)
        study.ml_analyzed_samples = last_end = study.samples_count
    return _finish_ml_pass(study, bank, last_end, totals, written, pushable, False)


def _log_skipped(study: Study, start: int, end: int) -> None:
    logger.warning(
        "ml_signal_without_run_skipped",
        study_id=str(study.id),
        start_sample=start,
        end_sample=end,
    )


def _finish_ml_pass(
    study: Study,
    bank: TemplateBank | None,
    last_end: int,
    totals: dict[str, float],
    written: int,
    pushable: Pushable | None,
    pending: bool,
) -> MlPass:
    if bank is not None:
        ml_persistence.store_bank(study, bank, last_end, totals)
    return MlPass(written=written, pushable=pushable, pending=pending)


async def _guarded[T](
    db: AsyncSession,
    study: Study,
    run: Callable[[], Awaitable[T]],
    *,
    fallback: T,
    failure_event: str,
    cursor: int,
) -> T:
    """Corre una pasada de análisis en un SAVEPOINT: si falla, falla solo ella.

    Las pasadas que van detrás de un cursor —el motor (`append_ml_analysis`) y
    los latidos de las métricas Holter (`append_beat_analysis`)— comparten la
    transacción con lo que el lote o el cierre ya escribieron: el segmento, la
    Capa A, la línea de tiempo, la vista filtrada, la compactación, y la otra
    pasada. Un error de una (un objeto que no está en S3, un caso que el
    detector no soporta) no puede llevárselos puestos: el lote quedaba `FAILED`,
    y como el cursor no avanzaba, cada lote siguiente del estudio fallaba en el
    mismo punto. Con el SAVEPOINT se deshace solo lo de esa pasada, su cursor
    queda donde estaba y el próximo lote o la próxima finalización lo vuelve a
    intentar; mientras tanto el informe lo frena (`BEAT_ANALYSIS_PENDING`,
    `ML_ANALYSIS_PENDING`).

    La contención de locks **no** se traga: no es una falla de la pasada sino
    de la transacción entera, y quien la maneja (`process_batch`, que deja el
    lote pendiente en vez de fallido) tiene que verla.

    `begin_nested` hace flush de lo pendiente antes de abrir el SAVEPOINT, así
    que lo anterior a la pasada queda a salvo en la transacción de afuera.
    Deshacerlo expira lo que se modificó adentro (el estudio: los cursores, los
    chunks), y en una sesión async un atributo expirado no se puede recargar
    solo; por eso el `refresh` explícito, y el id y el `cursor` del log se leen
    antes.
    """
    study_id = study.id
    try:
        async with db.begin_nested():
            return await run()
    except DBAPIError as error:
        if is_lock_contention(error):
            raise
        await _log_pass_failure(failure_event, study_id, cursor)
    except Exception:  # noqa: BLE001 — una pasada de análisis no puede frenar la ingesta
        await _log_pass_failure(failure_event, study_id, cursor)
    await db.refresh(study)
    return fallback


async def _log_pass_failure(event: str, study_id: uuid.UUID, cursor: int) -> None:
    await logger.aexception(event, study_id=str(study_id), cursor=cursor)


async def _guarded_ml_pass(
    db: AsyncSession, study: Study, batch: ECGBatch | None, *, flush_tail: bool | None = None
) -> MlPass:
    """`append_ml_analysis` en `_guarded`: si el motor falla, falla solo el motor."""
    return await _guarded(
        db,
        study,
        lambda: append_ml_analysis(db, study, batch, flush_tail=flush_tail),
        fallback=MlPass(),
        failure_event="ml_analysis_failed",
        cursor=study.ml_analyzed_samples,
    )


async def _guarded_beat_pass(
    db: AsyncSession, study: Study, *, max_samples: int | None = BEAT_SAMPLES_PER_PASS
) -> None:
    """`append_beat_analysis` en `_guarded`: un error de los latidos de las
    métricas no tira abajo el lote, su Capa A ni el motor."""
    await _guarded(
        db,
        study,
        lambda: append_beat_analysis(db, study, max_samples=max_samples),
        fallback=None,
        failure_event="beat_analysis_failed",
        cursor=study.beats_analyzed_samples,
    )


async def _compact_closed_beats(study: Study) -> None:
    """La compactación final de los latidos, una vez que su cursor llegó al final."""
    study.ecg_beat_chunks = await asyncio.to_thread(compact_beat_chunks, study, force=True)


async def _process_one_batch(
    db: AsyncSession, study: Study, batch: ECGBatch
) -> tuple[int, int, Pushable | None]:
    """Procesa un lote con el estudio ya bloqueado; no maneja la transacción."""
    if batch.frames_s3_key is None:
        raise RuntimeError("El lote no tiene tramas archivadas.")
    key = segment_key(study.id, batch.first_seq or 0)
    appended = next((s for s in study.ecg_segments if s.get("key") == key), None)
    if appended is not None:
        # El segmento ya está en el estudio: un procesamiento anterior lo anexó
        # y commiteó (segmento, muestras, línea de tiempo y eventos van en la
        # misma transacción), pero el estado del lote quedó en otro valor.
        # Anexarlo de nuevo lo corría al final de `ecg_segments` con la misma
        # clave y dejaba un hueco donde estaba: la vista filtrada fallaba ahí en
        # cada reintento, y el drenaje, que va en orden, no pasaba de este lote.
        # Le pasó a un estudio real del chaleco el 1/10/2026, con 62 lotes
        # trabados detrás.
        samples = int(appended["sampleCount"])
        await logger.awarning(
            "process_batch_already_appended",
            batch_id=str(batch.id),
            study_id=str(study.id),
            previous_status=batch.processing_status.value,
        )
        batch.num_samples = samples
        batch.processing_status = ProcessingStatus.DONE
        batch.processing_error = None
        return samples, 0, None
    batch.processing_status = ProcessingStatus.PROCESSING
    await db.flush()

    # Las llamadas a S3 de acá abajo van a un hilo (`run_io`): en el event loop
    # congelan los demás requests de la misma instancia mientras el lote se
    # procesa, incluidos los POST de otros chalecos.
    decoded = await run_io(_read_batch, batch.frames_s3_key)
    sample_rate = study.sample_rate or 500

    # --- Segmento ---------------------------------------------------------- #
    start_sample_index = study.samples_count
    payload = decoded.signal_mV.tobytes()
    await run_io(put_object, key, payload)
    flags_payload = decoded.flags.astype(np.uint8).tobytes()
    flags_object = flags_key(study.id, batch.first_seq or 0)
    await run_io(put_object, flags_object, flags_payload)

    segments = list(study.ecg_segments)
    segments.append(
        _object_meta(
            key,
            payload,
            startSampleIndex=start_sample_index,
            sampleCount=decoded.n_samples,
            # Marca que este lote archivó sus flags (`flags_key` sale de acá) y
            # por qué lote atribuir lo que el motor escriba en el cierre. Un
            # número y no las dos claves: este JSONB se reescribe entero en cada
            # lote y viaja en cada `select(Study)`.
            firstSeq=batch.first_seq or 0,
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
        await run_io(put_object, envelope_key(study.id, batch.first_seq or 0), envelope.tobytes())
    study.ecg_envelope_carry = remainder.tobytes() if remainder.size else None
    # Antes acá se llamaba `rebuild_pyramid`, que releía de S3 TODAS las
    # envolventes del estudio en cada lote. Era una fuente de contención que
    # crecía con el estudio y corría con la fila bloqueada.
    study.ecg_pyramid_levels = append_level_chunks(study, envelope, batch.first_seq or 0)
    study.ecg_pyramid_levels = compact_pyramid(study)
    # Red de seguridad: las columnas JSONB no rastrean mutaciones internas.
    flag_modified(study, "ecg_segments")
    flag_modified(study, "ecg_pyramid_levels")

    # --- Línea de tiempo de pared ------------------------------------------ #
    gap_ms, previous_end_t0_ms = await _place_on_timeline(
        db, study, batch, decoded, start_sample_index
    )
    if batch.preceding_seq_gap_frames > 0:
        # Faltan tramas antes de este lote: el salto de `t0Ms` ya lo explica el
        # evento de `signal_loss_events`, y marcarlo además como `frame_gap`
        # sería contar dos veces el mismo hueco. `frame_gap` es adquisición
        # perdida **con `seq` contiguo**, igual que adentro de un lote.
        previous_end_t0_ms = None
    await append_filtered_view(db, study)
    # En `_guarded`, como el motor: si los latidos de las métricas fallan, el
    # lote igual queda `DONE` con su segmento, su Capa A y la vista filtrada.
    await _guarded_beat_pass(db, study)

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

    # Los huecos van primero: son lo que NO está, y leerlos antes que los
    # hallazgos sobre la señal que sí llegó es el orden en que hay que mirarlos.
    created, pushable = await _persist_events(
        db,
        batch,
        study,
        signal_loss_events(batch, gap_ms)
        + derive_events(decoded, sample_rate, previous_end_ms=previous_end_t0_ms),
        start_sample_index,
        sample_rate,
    )
    # El motor no corre acá sino una vez por transacción, después de drenar
    # todos los lotes pendientes (`process_batch`).
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
            if not is_lock_contention(error):
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
            pushable = most_severe(pushable, batch_pushable)

        if processed:
            # Después de `_persist_events` a propósito: los `frame_gap` /
            # `internal_gap` de los lotes drenados ya están en la sesión, y el
            # motor los lee como empalmes del bloque. Analiza solo los bloques
            # que esos lotes completaron (casi siempre ninguno: un bloque son
            # ~20 lotes); el resto espera al siguiente o al cierre. **Una**
            # pasada por transacción y no una por lote: el presupuesto de la
            # pasada (`ML_PASS_BUDGET_SECONDS`) es lo que mantiene la fila
            # debajo del `lock_timeout`, y con una pasada por lote un drenaje de
            # N lotes atrasados lo multiplicaba por N. El cursor hace que el
            # resultado sea el mismo; lo escrito se atribuye al último lote. En
            # un SAVEPOINT: una falla del motor no puede tirar abajo los lotes.
            last_batch, last_samples, last_events = processed[-1]
            ml_pass = await _guarded_ml_pass(db, study, last_batch)
            pushable = most_severe(pushable, ml_pass.pushable)
            processed[-1] = (last_batch, last_samples, last_events + ml_pass.written)
            # Recuento y no `+=`: los encabezados por morfología del motor se
            # upsertean (la fila ya existe y solo crece su conteo), así que
            # "filas escritas" no es "eventos nuevos". Contar las filas del
            # estudio cuenta cada evento una sola vez, lo haya escrito la Capa A
            # o el motor.
            await ml_persistence.recount_events(db, study)

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
        if not is_lock_contention(error):
            await _mark_failed(db, batch_id, failed_batch_id, error)
            return
        # No se pudo tomar la fila ni después de los reintentos, o Postgres
        # cortó un deadlock eligiendo esta transacción. El lote NO es
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


async def process_study_task(study_id: uuid.UUID, *, flush_open_tail: bool = False) -> None:
    """Retry archived batches, finish the filtered and analyzed tails, and advance beat analysis.

    The study row lock and DONE status keep concurrent retries idempotent. This
    also gives a failed final background job a recovery path through the
    manifest request or an explicit study close.

    El motor corre acá con `batch=None`: con el estudio cerrado, la cola de la
    última corrida —lo que no llegó a un bloque entero— ya se puede analizar.
    `flush_open_tail` la analiza también con el estudio en curso: es lo que usa
    `flush_stale_tails` cuando la corrida lleva demasiado sin crecer.
    Una pasada analiza como mucho `ml_analysis_max_blocks_per_pass` bloques con
    la fila tomada; si quedó más (un estudio que el motor nunca vio), commitea,
    suelta la fila y vuelve a tomarla, así la ingesta de otro lote no espera
    minutos detrás de esto. La fusión de morfologías y la compactación van
    recién cuando el motor terminó: fundir antes vería un banco incompleto.

    El motor y el análisis de latidos de las métricas Holter corren cada uno en
    su SAVEPOINT (`_guarded`): si uno falla, la cola de la vista filtrada, la
    otra pasada y la compactación del cierre se commitean igual.

    El análisis de latidos (`append_beat_analysis`) avanza en cada vuelta,
    acotado a `BEAT_SAMPLES_PER_PASS`, y su compactación final va con la del
    cierre (`_finalize_closed_study`).
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        pending = await repo.list_batches_to_process(session, study_id)
        if pending:
            await process_batch(session, pending[0].id)
        await session.rollback()
        try:
            while True:
                study = await _lock_study(session, study_id)
                if study is None:
                    return
                await append_filtered_view(session, study)
                # También al cerrar hay que acotar la pasada: un estudio anterior a
                # este análisis puede tener días de señal pendiente. El backfill o
                # una visita posterior al manifest retoman desde el cursor guardado.
                await _guarded_beat_pass(session, study, max_samples=BEAT_SAMPLES_PER_PASS)
                ml_pass = await _guarded_ml_pass(
                    session,
                    study,
                    None,
                    flush_tail=flush_open_tail or study.status is not StudyStatus.IN_PROGRESS,
                )
                if ml_pass.written:
                    await ml_persistence.recount_events(session, study)
                if not ml_pass.pending:
                    await _finalize_closed_study(study, session)
                patient_id = study.patient_id
                await session.commit()
                # Igual que `process_batch`: el aviso sale con la transacción
                # cerrada, cuando el `alertId` ya existe.
                if ml_pass.pushable is not None:
                    await notify_patient_task(
                        patient_id,
                        anomaly_message(
                            ml_pass.pushable.alert_id,
                            datetime.now(UTC).isoformat(),
                            ml_pass.pushable.kind,
                        ),
                    )
                if not ml_pass.pending:
                    return
        except Exception:
            await session.rollback()
            logger.exception("process_study_recovery_failed", study_id=str(study_id))


async def flush_stale_tails(now: datetime | None = None) -> list[uuid.UUID]:
    """Analiza la cola de las corridas abiertas que hace rato no crecen. Devuelve los estudios.

    El motor deja la cola de la corrida abierta —hasta un bloque menos una
    muestra— esperando a que el lote siguiente complete el bloque. Mientras el
    chaleco sube cada diez minutos, eso es una demora de un ciclo. Pero si deja
    de subir —el paciente salió de su casa, se cayó el router, se agotó la
    batería y el estudio sigue en curso—, una pausa crítica que **ya llegó** al
    servidor no se analizaba hasta que volvieran los datos o el médico cerrara
    el estudio: horas o días sin aviso. Por lote, antes, se avisaba al subir.

    Una corrida sin lotes nuevos desde hace `ml_open_tail_flush_minutes` se
    analiza hasta su última muestra como si estuviera cerrada, después de
    drenar los lotes que llegaron y quedaron sin procesar (un `BackgroundTask`
    perdido, una pasada que no consiguió la fila). Si después sigue
    creciendo, el bloque siguiente arranca en el cursor con su contexto
    izquierdo, y un episodio que cruce ese borde se empalma como cualquier otro:
    lo único que depende del reloj es dónde cae ese borde.
    """
    from app.db.session import async_session_factory

    minutes = settings.ml_open_tail_flush_minutes
    if not settings.ml_enabled or minutes <= 0:
        return []
    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=minutes)
    async with async_session_factory() as session:
        study_ids = await repo.list_studies_with_stale_tail(session, cutoff)
    for study_id in study_ids:
        await process_study_task(study_id, flush_open_tail=True)
    return study_ids


#: Cada cuánto busca colas viejas el lazo del `lifespan`. Es una consulta
#: sobre los estudios en curso, que son pocos.
STALE_TAIL_SWEEP_SECONDS = 60.0


async def sweep_stale_tails_forever() -> None:
    """El lazo que corre `flush_stale_tails` mientras viva el proceso (`app.main.lifespan`).

    Con varios procesos corre en cada uno: el lock de la fila y el cursor hacen
    que el segundo que llega no encuentre nada que analizar.
    """
    while True:
        await asyncio.sleep(STALE_TAIL_SWEEP_SECONDS)
        try:
            await flush_stale_tails()
        except Exception:  # noqa: BLE001 — el lazo no puede morir por una pasada
            logger.exception("stale_tail_sweep_failed")


async def _finalize_closed_study(study: Study, session: AsyncSession) -> None:
    if study.status is StudyStatus.COMPLETED:
        # Acá y no en el cierre: es el único punto por el que pasan todos los
        # caminos que completan un estudio (el médico, la desasignación del
        # equipo y el rebobinado de `seq` de la ingesta), y corre después de
        # drenar los lotes pendientes y de analizar la cola de la última
        # corrida. Un estudio se puede cerrar con lotes en cola, y fundir antes
        # vería un banco incompleto. Sin plantillas que fundir no escribe nada.
        await ml_persistence.consolidate_morphologies(session, study)
    if study.status is not StudyStatus.IN_PROGRESS:
        if study.beats_analyzed_samples >= study.samples_count:
            # Guardada como la pasada: una compactación de latidos que falla
            # (un chunk que no está en S3) no puede deshacer la cola del motor
            # ni la fusión de morfologías que van en esta misma transacción.
            await _guarded(
                session,
                study,
                lambda: _compact_closed_beats(study),
                fallback=None,
                failure_event="beat_compaction_failed",
                cursor=study.beats_analyzed_samples,
            )
        study.ecg_pyramid_levels = await asyncio.to_thread(compact_pyramid, study, force=True)
        if study.filter_view_enabled:
            study.ecg_filtered_pyramid_levels = await asyncio.to_thread(
                compact_pyramid, study, force=True, filtered=True
            )
