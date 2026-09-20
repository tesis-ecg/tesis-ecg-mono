"""Construcción de la línea de tiempo de pared de un estudio.

El buffer de muestras de un estudio es continuo por construcción: cada lote se
pega al anterior. La grabación no lo es. Este módulo es el que traduce entre las
dos cosas, agrupando los lotes en **tramos** (ver `StudyTimelineSegment`) y
poniéndole a cada uno su hora real.

Corre en el procesamiento y no en la ruta del ACK a propósito: acá
`study.samples_count` ya es autoritativo, así que el índice de muestra con el
que arranca cada tramo no hay que estimarlo.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.db.models.ecg_batch import ECGBatch
from app.db.models.study import Study
from app.db.models.study_timeline_segment import StudyTimelineSegment, TimeSyncSource

#: Cota de la pendiente del ajuste de deriva. Un cristal de cuarzo comercial
#: anda en decenas de ppm; 200 ppm es holgado para cualquier equipo sano y
#: angosto para que una sincronización SNTP mala no deforme el estudio.
MAX_DRIFT_PPM = 200

#: Anclas mínimas para ajustar una recta. Con menos, la pendiente estaría
#: dominada por el ruido de una sola medición y sería peor que no corregir.
MIN_ANCHORS_FOR_FIT = 3


@dataclass(frozen=True)
class BatchTiming:
    """Coordenadas temporales de un lote, leídas de sus tramas."""

    boot_id: int | None
    first_t0_ms: int
    last_t0_ms: int
    last_end_t0_ms: int
    first_seq: int | None
    last_seq: int | None


def batch_timing(
    batch: ECGBatch, first_t0_ms: int, last_t0_ms: int, last_duration_ms: int
) -> BatchTiming:
    return BatchTiming(
        boot_id=batch.boot_id,
        first_t0_ms=first_t0_ms,
        last_t0_ms=last_t0_ms,
        last_end_t0_ms=last_t0_ms + last_duration_ms,
        first_seq=batch.first_seq,
        last_seq=batch.last_seq,
    )


def starts_new_segment(
    last: StudyTimelineSegment | None, batch: ECGBatch, timing: BatchTiming
) -> bool:
    """Las tres reglas de `INTEGRACION.md` §5, en una sola función.

    1. **Cambia el `bootId`.** Reinicio, watchdog, cambio de batería: `t0Ms`
       volvió a cero y no tiene relación con el del tramo anterior.
    2. **`t0Ms` retrocede con el mismo `bootId`.** Es el wraparound de
       `millis()` a los 49,7 días. En un estudio de 15 o 30 días es cuestión de
       tiempo, y hay que distinguirlo del reinicio: acá el equipo no se reinició,
       solo dio la vuelta su contador.
    3. **Salto por encima de la tolerancia.** El equipo no estuvo grabando.

    La regla 3 se mide en **tiempo del equipo** y no en hora de pared, aunque el
    hueco que describe sea de hora de pared. Con el mismo `bootId` y `t0Ms`
    monótono los dos lotes vienen del mismo `millis()`, así que la distancia
    entre ellos es exacta y no la toca la red. Compararlos por su hora de pared
    sería comparar dos anclas distintas: cada `epoch_anchor_ms` lleva adentro la
    latencia de SU pedido, y con el ancla vieja —`recepción − uptime`, que es lo
    que corre mientras `ingest_require_time_sync` está apagado— esa latencia se
    mueve segundos entre lotes (5,1 s de mediana y picos de 22,8 s, informe de
    Biomédica del 8/9/2026). Un pico partía en dos una grabación continua, y el
    tramo nuevo arrancaba **antes** de que terminara el anterior.

    Un chaleco fuera del alcance del WiFi sigue grabando en su flash, así que no
    deja hueco y le corresponde un solo tramo. Uno apagado no puede dejar hueco
    sin cambiar el `bootId`, que es la regla 1. Lo que la regla 3 ve de verdad es
    el equipo despierto que dejó de grabar: ahí `t0Ms` salta y el salto se lee
    directo.
    """
    if last is None:
        return True
    if last.boot_id != timing.boot_id:
        return True
    if last.last_t0_ms is not None and timing.first_t0_ms < last.last_t0_ms:
        return True
    if last.last_t0_ms is None:
        # Tramo del backfill viejo, sin `t0Ms` crudo. Queda la comparación por
        # hora de pared, con su ruido: es lo único que se puede hacer con lo que
        # esos lotes archivaron.
        actual_start_ms = (batch.epoch_anchor_ms or 0) + timing.first_t0_ms
        return abs(actual_start_ms - last.end_epoch_ms) > settings.ingest_timeline_gap_tolerance_ms
    device_gap_ms = timing.first_t0_ms - t0_at(last, last.end_epoch_ms)
    return device_gap_ms > settings.ingest_timeline_gap_tolerance_ms


def gap_before_ms(last: StudyTimelineSegment | None, batch: ECGBatch, timing: BatchTiming) -> int:
    """Cuánto tiempo real pasó entre el final del tramo anterior y este lote.

    Es la **duración del hueco**, que es lo que el médico necesita ver cuando el
    equipo perdió señal (`INTEGRACION.md` §9.1: "registro de cada overflow con su
    hora de pared"). `starts_new_segment` decide *si* hay hueco; esto mide
    *cuánto*, con el mismo criterio y por el mismo motivo:

    - **Mismo `bootId`**: se mide en el reloj del equipo, que es exacto y no
      arrastra la latencia del pedido. Es el caso del overflow del log circular,
      donde el equipo siguió corriendo y solo se pisó señal.
    - **`bootId` distinto**: el `millis()` volvió a cero, así que las dos cifras
      no son comparables y la única referencia es la hora de pared. Trae el ruido
      de las anclas, pero ante un hueco de horas eso no cambia nada.

    Devuelve 0 si no hay tramo anterior o si el cálculo da negativo (anclas
    ruidosas que se solapan): un hueco negativo no existe.
    """
    if last is None:
        return 0
    if last.boot_id == timing.boot_id and last.last_t0_ms is not None:
        return max(timing.first_t0_ms - t0_at(last, last.end_epoch_ms), 0)
    actual_start_ms = (batch.epoch_anchor_ms or 0) + timing.first_t0_ms
    return max(actual_start_ms - last.end_epoch_ms, 0)


def fit_anchor(anchors: list[tuple[int, int]]) -> tuple[int, int]:
    """Ajusta `epoch = a + (1 + ppm/1e6)·t0` sobre las anclas de un mismo arranque.

    Devuelve `(boot_epoch_ms, slope_ppm)`.

    Hace falta porque un cristal de 20 ppm corre ~1,7 s por día: con una sola
    ancla, un estudio de 24 h no cierra en el objetivo de ±1 s. Como llega un
    ancla por ciclo de envío, hay material de sobra para la recta.

    Mínimos cuadrados a mano y no numpy: este módulo lo importa el procesamiento,
    pero la cuenta es sobre un puñado de puntos y no justifica la dependencia.
    La pendiente se acota a ±`MAX_DRIFT_PPM` para que una sincronización mala no
    arrastre el estudio entero.
    """
    if not anchors:
        return 0, 0
    if len(anchors) < MIN_ANCHORS_FOR_FIT:
        # Sin material para una recta, el ancla más reciente es la mejor
        # estimación: es la que menos deriva acumuló desde su sincronización.
        t0, epoch = anchors[-1]
        return epoch - t0, 0

    n = len(anchors)
    mean_t = sum(t for t, _ in anchors) / n
    mean_e = sum(e for _, e in anchors) / n
    var = sum((t - mean_t) ** 2 for t, _ in anchors)
    if var == 0:
        return round(mean_e - mean_t), 0
    cov = sum((t - mean_t) * (e - mean_e) for t, e in anchors)
    slope = cov / var
    ppm = max(-MAX_DRIFT_PPM, min(MAX_DRIFT_PPM, round((slope - 1) * 1_000_000)))
    slope = 1 + ppm / 1_000_000
    intercept = mean_e - slope * mean_t
    return round(intercept), ppm


def epoch_at(segment: StudyTimelineSegment, t0_ms: int) -> int:
    """Hora de pared de un `t0Ms` dentro de este tramo, con la deriva aplicada."""
    return round(segment.boot_epoch_ms + t0_ms * (1 + segment.anchor_slope_ppm / 1_000_000))


def t0_at(segment: StudyTimelineSegment, epoch_ms: int) -> int:
    """Inversa de `epoch_at`: el `millis()` del equipo que dio esa hora.

    Es lo que permite volver al reloj del equipo desde lo que quedó archivado,
    sin guardar una columna más: `end_epoch_ms` se escribió con `epoch_at`, así
    que deshacer la cuenta devuelve el `t0Ms` con el que se calculó.
    """
    return round((epoch_ms - segment.boot_epoch_ms) / (1 + segment.anchor_slope_ppm / 1_000_000))


def open_segment(
    study: Study,
    batch: ECGBatch,
    timing: BatchTiming,
    ordinal: int,
    start_sample_index: int,
    sample_count: int,
) -> StudyTimelineSegment:
    """Abre un tramo nuevo.

    `start_sample_index` viene explícito y no de `study.samples_count`: cuando
    esto corre, el caller ya sumó las muestras del lote al estudio, así que
    leerlo de ahí pondría el tramo a empezar donde termina.
    """
    boot_epoch_ms = batch.epoch_anchor_ms or 0
    segment = StudyTimelineSegment(
        study_id=study.id,
        ordinal=ordinal,
        boot_id=timing.boot_id,
        first_seq=timing.first_seq,
        last_seq=timing.last_seq,
        start_sample_index=start_sample_index,
        sample_count=sample_count,
        first_t0_ms=timing.first_t0_ms,
        last_t0_ms=timing.last_t0_ms,
        boot_epoch_ms=boot_epoch_ms,
        anchor_slope_ppm=0,
        anchor_source=batch.time_sync_source or TimeSyncSource.SERVER_RECEIVE,
        anchor_uncertainty_ms=batch.time_sync_uncertainty_ms,
        start_epoch_ms=boot_epoch_ms + timing.first_t0_ms,
        end_epoch_ms=boot_epoch_ms + timing.last_end_t0_ms,
    )
    return segment


def extend_segment(
    segment: StudyTimelineSegment,
    batch: ECGBatch,
    timing: BatchTiming,
    sample_count: int,
    anchors: list[tuple[int, int]],
) -> None:
    """Suma este lote al tramo abierto y re-ajusta su ancla.

    El re-ajuste es lo que hace que la hora **mejore** a medida que llegan más
    lotes, en vez de quedar clavada a la primera sincronización del arranque. Se
    recalculan `start_epoch_ms` y `end_epoch_ms` desde el ancla nueva para que el
    tramo entero quede coherente y no mezcle dos anclas distintas.
    """
    if anchors:
        segment.boot_epoch_ms, segment.anchor_slope_ppm = fit_anchor(anchors)
    if batch.time_sync_source is not None:
        segment.anchor_source = batch.time_sync_source
    segment.anchor_uncertainty_ms = batch.time_sync_uncertainty_ms
    segment.last_seq = timing.last_seq
    segment.last_t0_ms = timing.last_t0_ms
    segment.sample_count += sample_count
    if segment.first_t0_ms is not None:
        # Sin `t0Ms` crudo no hay desde dónde recalcular, y `or 0` pondría el
        # tramo a empezar en el arranque del equipo — horas antes de su primera
        # muestra. Se conserva la hora con la que se abrió.
        segment.start_epoch_ms = epoch_at(segment, segment.first_t0_ms)
    segment.end_epoch_ms = epoch_at(segment, timing.last_end_t0_ms)
