"""Serie R-R y prematuridad.

La serie R-R es la entrada de las dos cosas que vienen después: las reglas de
ritmo (`arrhythmia.py`) y el eje de **prematuridad** del score de anomalía
(`morphology.py`). Un latido ectópico no se define solo por tener otra forma:
llega **antes de tiempo**. Morfología rara sin prematuridad es, casi siempre, un
artefacto de movimiento.

Las métricas clínicas de variabilidad (SDNN, RMSSD, pNN50, dominio frecuencial)
son un requerimiento **Should** de `Requerimientos.md` §6.B y quedan fuera de
este alcance; acá solo está el resumen que alimenta `ml_state.metrics`.

No se usa `nk.hrv_time`: devuelve un `DataFrame` de pandas —lento, y `Any` para
mypy— por seis fórmulas de una línea.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.ml.contracts import Floats, Indices, Mask

#: Latidos de la mediana móvil que define "el ritmo de este momento". 32 latidos
#: son ~30 s: largo para que una extrasístole aislada no mueva la referencia,
#: corto para seguir un cambio real de frecuencia.
LOCAL_WINDOW_BEATS = 32


@dataclass(frozen=True, slots=True)
class RRSeries:
    """Intervalos entre R consecutivos. `rr_seconds[i]` va de `rpeaks[i]` a `[i+1]`."""

    rpeaks: Indices
    rr_seconds: Floats
    #: Verdadero si el intervalo cae **entero** en señal analizable. Un R-R que
    #: cruza un tramo de electrodo despegado no mide una pausa: mide que faltan
    #: latidos que sí ocurrieron.
    valid: Mask

    @property
    def n_beats(self) -> int:
        return int(self.rpeaks.size)


def build_rr(rpeaks: Indices, analyzable: Mask, sample_rate: int) -> RRSeries:
    if rpeaks.size < 2:
        return RRSeries(
            rpeaks=rpeaks,
            rr_seconds=np.empty(0, dtype=np.float32),
            valid=np.empty(0, dtype=bool),
        )
    rr = np.diff(rpeaks).astype(np.float32) / float(sample_rate)

    # Un intervalo es válido si todo el tramo entre los dos R es analizable. Se
    # resuelve con la suma acumulada de la máscara: comparar el conteo de
    # muestras buenas contra el largo del intervalo es O(n) para todos a la vez.
    cumulative = np.concatenate(([0], np.cumsum(analyzable.astype(np.int64))))
    starts = rpeaks[:-1]
    ends = rpeaks[1:]
    good_samples = cumulative[ends] - cumulative[starts]
    valid = good_samples == (ends - starts)
    return RRSeries(rpeaks=rpeaks, rr_seconds=rr, valid=valid.astype(bool))


def local_median_rr(rr_seconds: Floats, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Mediana móvil centrada de la serie R-R, del mismo largo que la entrada.

    Es la referencia contra la que se mide la prematuridad. Tiene que ser
    **local**: la frecuencia cardíaca de un paciente cambia por un factor de dos
    entre dormir y subir una escalera, y una referencia global marcaría toda la
    actividad diurna como prematura.
    """
    if rr_seconds.size == 0:
        return np.empty(0, dtype=np.float32)
    if rr_seconds.size <= window_beats:
        return np.full(rr_seconds.size, float(np.median(rr_seconds)), dtype=np.float32)

    half = window_beats // 2
    padded = np.pad(rr_seconds.astype(np.float64), (half, window_beats - half - 1), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window_beats)
    return np.asarray(np.median(windows, axis=-1), dtype=np.float32)


def prematurity(rr: RRSeries, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Por latido: `RR_precedente / RR_medio_local`. Menor que 1 = prematuro.

    El primer latido no tiene intervalo precedente y queda en 1,0 (neutro): no
    hay evidencia de que sea prematuro, y asumir lo contrario lo marcaría por el
    solo hecho de abrir el lote.
    """
    result = np.ones(rr.n_beats, dtype=np.float32)
    if rr.rr_seconds.size == 0:
        return result
    reference = local_median_rr(rr.rr_seconds, window_beats)
    safe = np.where(reference > 0, reference, np.float32(1.0))
    result[1:] = rr.rr_seconds / safe
    return result


def instantaneous_bpm(rr_seconds: Floats) -> Floats:
    if rr_seconds.size == 0:
        return np.empty(0, dtype=np.float32)
    safe = np.where(rr_seconds > 0, rr_seconds, np.float32(np.inf))
    return np.asarray(60.0 / safe, dtype=np.float32)


def hrv_summary(rr: RRSeries) -> dict[str, float]:
    """Resumen sobre los intervalos válidos. Vacío si no hay ninguno."""
    valid = rr.rr_seconds[rr.valid] if rr.valid.size else rr.rr_seconds
    if valid.size < 2:
        return {}
    diffs = np.diff(valid.astype(np.float64))
    bpm = instantaneous_bpm(valid)
    return {
        "beats": float(rr.n_beats),
        "meanNNms": round(float(np.mean(valid) * 1000), 3),
        "sdnnMs": round(float(np.std(valid, ddof=1) * 1000), 3),
        "rmssdMs": round(float(np.sqrt(np.mean(diffs**2)) * 1000), 3),
        "pnn50": round(float(np.mean(np.abs(diffs) > 0.05)), 6),
        "meanBpm": round(float(np.mean(bpm)), 3),
        "minBpm": round(float(np.min(bpm)), 3),
        "maxBpm": round(float(np.max(bpm)), 3),
    }
