"""Serie R-R, prematuridad y variabilidad de la frecuencia cardíaca.

Dos consumidores, todavía sin unificar; las cifras que ve el médico en el
informe Holter salen de la segunda parte.

**Serie R-R y prematuridad (motor de detección).** La serie R-R es la entrada
de las dos cosas que vienen después: las reglas de ritmo (`arrhythmia.py`) y el
eje de **prematuridad** del score de anomalía (`morphology.py`). Un latido
ectópico no se define solo por tener otra forma: llega **antes de tiempo**.
Morfología rara sin prematuridad es, casi siempre, un artefacto de movimiento.

Las métricas de variabilidad en el dominio del tiempo (SDNN, RMSSD, pNN50) del
motor viven acá en dos versiones que tienen que coincidir: `hrv_summary`,
directa sobre una serie, y los acumuladores de `totals.py`, que suman bloques y
dan el valor exacto del estudio entero. Las dos cuentan los mismos intervalos
(`counted_intervals`) y las mismas diferencias (`successive_pairs`).

No se usa `nk.hrv_time`: devuelve un `DataFrame` de pandas —lento, y `Any` para
mypy— por seis fórmulas de una línea.

**VFC del informe Holter (`time_domain`, `frequency_domain`).** Sigue las
definiciones del Task Force de la ESC/NASPE (1996):

- **Tiempo:** SDNN, SDANN (desvío de las medias de 5 min), rMSSD, pNN50 y el
  coeficiente de variación CV = SDNN / NN medio.
- **Frecuencia:** VLF, LF y HF salen del promedio de los espectros de ventanas de
  5 min. Cada ventana se remuestrea a 4 Hz con spline cúbica, se le quita la tendencia lineal y se
  le aplica una ventana de Hann. ULF sale del espectro de la serie de medias de
  5 min cuando son consecutivas: con un punto cada 300 s, esa serie cae entera
  en la banda < 0,0033 Hz. Si hay ventanas sin señal, ULF queda sin calcular.
  La energía total suma las bandas disponibles, como en los informes comerciales.

Estas funciones son puras; reciben NN en ms y su instante en s.

**Ojo con las unidades del pNN50:** `time_domain` lo devuelve en porcentaje
(`pnn50Percent`, 0–100) y `hrv_summary`/`totals.py` como fracción (`pnn50`,
0–1). Mientras los totales del motor sean internos no choca; compararlos o
exponerlos sin convertir es un error de 100×.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import CubicSpline
from scipy.signal import periodogram

from app.ml.contracts import Floats, Indices, Mask

#: Latidos de la mediana móvil que define "el ritmo de este momento". 32 latidos
#: son ~30 s: largo para que una extrasístole aislada no mueva la referencia,
#: corto para seguir un cambio real de frecuencia.
LOCAL_WINDOW_BEATS = 32

#: Prematuridad por debajo de la cual un latido **no es normal** para la
#: variabilidad: llegó con un R-R un 20 % más corto que el ritmo del momento. Es
#: el criterio clásico de edición de la serie NN (excluir lo que se aparta más
#: de un 20 %), aplicado solo hacia el lado prematuro: el intervalo largo que
#: sigue a un ectópico ya sale por tocar al ectópico, y un R-R largo sin
#: ectópico es una pausa o una bradicardia, que es variabilidad real.
ECTOPIC_PREMATURITY = 0.8


@dataclass(frozen=True, slots=True)
class RRSeries:
    """Intervalos entre R consecutivos. `rr_seconds[i]` va de `rpeaks[i]` a `[i+1]`."""

    rpeaks: Indices
    rr_seconds: Floats
    #: Verdadero si el intervalo cae **entero** en señal analizable. Un R-R que
    #: cruza un tramo de electrodo despegado no mide una pausa: mide que faltan
    #: latidos que sí ocurrieron.
    valid: Mask
    #: La de `rpeaks`. Hace falta para medir en milisegundos sin pasar por
    #: `rr_seconds`, que es float32 (ver `nn_milliseconds`).
    sample_rate: int

    @property
    def n_beats(self) -> int:
        return int(self.rpeaks.size)


def build_rr(rpeaks: Indices, analyzable: Mask, sample_rate: int) -> RRSeries:
    if rpeaks.size < 2:
        return RRSeries(
            rpeaks=rpeaks,
            rr_seconds=np.empty(0, dtype=np.float32),
            valid=np.empty(0, dtype=bool),
            sample_rate=sample_rate,
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
    return RRSeries(rpeaks=rpeaks, rr_seconds=rr, valid=valid.astype(bool), sample_rate=sample_rate)


def local_median_rr(rr_seconds: Floats, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Mediana móvil centrada de la serie R-R, del mismo largo que la entrada.

    Es la referencia contra la que se mide la prematuridad. Tiene que ser
    **local**: la frecuencia cardíaca de un paciente cambia por un factor de dos
    entre dormir y subir una escalera, y una referencia global marcaría toda la
    actividad diurna como prematura.

    Centrada quiere decir que mira `window_beats // 2` latidos **hacia
    adelante**, y en el final de la serie los repite (`mode="edge"`). Un bloque
    del cursor que terminara justo en un ectópico se mediría contra su propio
    R-R corto: prematuridad ~1, y el ectópico entraba en la variabilidad. Por
    eso los bloques se analizan con contexto derecho
    (`settings.ml_analysis_lookahead_seconds`), largo para esos latidos.
    """
    if rr_seconds.size == 0:
        return np.empty(0, dtype=np.float32)
    if rr_seconds.size <= window_beats:
        return np.full(rr_seconds.size, float(np.median(rr_seconds)), dtype=np.float32)

    half = window_beats // 2
    padded = np.pad(rr_seconds.astype(np.float64), (half, window_beats - half - 1), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window_beats)
    return np.asarray(np.median(windows, axis=-1), dtype=np.float32)


def expected_rr(rr: RRSeries, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Por latido: el R-R con que se lo esperaba, en segundos. NaN el primero.

    Es la mediana local del intervalo que lo trae (`local_median_rr`): la
    referencia contra la que `prematurity` mide si llegó antes de tiempo. La
    morfología la usa además para saber cuánto de la ventana del latido es suyo
    a esa frecuencia (`morphology.scoring_window`). El primer latido no tiene
    intervalo precedente, así que tampoco tiene un R-R esperado.
    """
    result = np.full(rr.n_beats, np.nan, dtype=np.float32)
    if rr.rr_seconds.size == 0:
        return result
    result[1:] = local_median_rr(rr.rr_seconds, window_beats)
    return result


def prematurity(rr: RRSeries, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Por latido: `RR_precedente / RR_esperado` (`expected_rr`). Menor que 1 = prematuro.

    El primer latido no tiene intervalo precedente y queda en 1,0 (neutro): no
    hay evidencia de que sea prematuro, y asumir lo contrario lo marcaría por el
    solo hecho de abrir el lote.
    """
    result = np.ones(rr.n_beats, dtype=np.float32)
    if rr.rr_seconds.size == 0:
        return result
    reference = expected_rr(rr, window_beats)[1:]
    safe = np.where(reference > 0, reference, np.float32(1.0))
    result[1:] = rr.rr_seconds / safe
    return result


def instantaneous_bpm(rr_seconds: Floats) -> Floats:
    if rr_seconds.size == 0:
        return np.empty(0, dtype=np.float32)
    safe = np.where(rr_seconds > 0, rr_seconds, np.float32(np.inf))
    return np.asarray(60.0 / safe, dtype=np.float32)


def normal_intervals(rr: RRSeries) -> Mask:
    """Por intervalo: verdadero si es **NN** —normal a normal— y no solo R-R.

    SDNN, RMSSD y pNN50 se definen sobre intervalos entre latidos normales. Un
    ectópico deja dos intervalos que no son variabilidad del nodo sinusal: el
    corto que lo trae y la pausa compensadora que lo sigue. Contados, un
    registro a 60 lpm exactos con un ectópico cada doce latidos daba un RMSSD de
    284 ms —un tono vagal de atleta— donde el real es cero. Sale el intervalo
    que toca un latido prematuro (`ECTOPIC_PREMATURITY`) en cualquiera de sus
    dos puntas.
    """
    if rr.rr_seconds.size == 0:
        return np.empty(0, dtype=bool)
    premature = prematurity(rr) < ECTOPIC_PREMATURITY
    return np.asarray(~(premature[:-1] | premature[1:]), dtype=bool)


def counted_intervals(rr: RRSeries, from_sample: int = 0, to_sample: int | None = None) -> Mask:
    """Por intervalo: verdadero si entra en las métricas de variabilidad.

    Un R-R cuenta si es válido, es NN (`normal_intervals`) y su latido
    **final** cae en `[from_sample, to_sample)`. Es la regla que hace sumables
    los bloques del cursor: cada intervalo tiene un solo latido final, ese
    latido cae en la parte nueva de un solo bloque, y así ningún intervalo se
    cuenta dos veces aunque los contextos de los bloques vecinos lo vuelvan a
    ver. El que empieza en el contexto izquierdo y termina en la parte nueva
    cuenta acá; el que termina en el contexto derecho, en el bloque siguiente.
    `to_sample` en `None` es hasta el final de la serie.
    """
    if rr.valid.size == 0:
        return np.empty(0, dtype=bool)
    ends = rr.rpeaks[1:]
    inside = ends >= from_sample
    if to_sample is not None:
        inside &= ends < to_sample
    return np.asarray(rr.valid & normal_intervals(rr) & inside, dtype=bool)


def successive_pairs(rr: RRSeries, counted: Mask) -> Mask:
    """Por intervalo `i`: verdadero si la diferencia `(RR[i-1], RR[i])` cuenta.

    Los dos tienen que ser válidos, NN y **adyacentes** —consecutivos en la
    serie, sin un intervalo descartado entre los dos—, y el segundo tiene que
    contar (`counted_intervals`). El primero puede estar en el contexto: la
    diferencia se atribuye al bloque de su segundo intervalo, por la misma
    razón que el intervalo se atribuye al de su latido final.

    Diferenciar la serie de válidos concatenada, que es lo que hacía
    `hrv_summary` antes, une los dos lados de cada tramo descartado: el último
    R-R antes de un electrodo despegado contra el primero después, minutos más
    tarde, como si fueran latidos seguidos. Eso infla el RMSSD justo en los
    registros con más artefactos.
    """
    pairs = np.zeros(counted.size, dtype=bool)
    if counted.size >= 2:
        pairs[1:] = counted[1:] & (rr.valid & normal_intervals(rr))[:-1]
    return pairs


def nn_milliseconds(rr: RRSeries) -> NDArray[np.float64]:
    """La serie R-R en milisegundos, en float64: la base de todas las sumas.

    Sale de la diferencia **entera** de muestras y no de `rr_seconds`. A 500 Hz
    cada intervalo es un múltiplo exacto de 2 ms, así que una diferencia
    sucesiva de exactamente 50 ms (25 muestras) no es un caso de borde sino un
    valor que aparece seguido. Pasando por float32 queda en 49,99998 o en
    50,00002 según el intervalo, y el pNN50 —que cuenta las que **superan**
    50 ms— se corría hasta medio punto por redondeo.
    """
    if rr.rpeaks.size < 2:
        return np.empty(0, dtype=np.float64)
    return np.diff(rr.rpeaks).astype(np.float64) * 1000.0 / float(rr.sample_rate)


def hrv_summary(rr: RRSeries) -> dict[str, float]:
    """Resumen directo sobre los intervalos NN válidos de **una** serie. Vacío si no hay dos.

    Es la cuenta de referencia, con numpy y sin acumuladores: el estudio se
    resume con `totals.summary_from_totals`, que tiene que dar lo mismo sobre un
    solo bloque sin contexto y es lo que verifica que las sumas estén bien. Las
    diferencias sucesivas usan la adyacencia de `successive_pairs`.
    """
    counted = counted_intervals(rr)
    if int(np.count_nonzero(counted)) < 2:
        return {}
    nn_ms = nn_milliseconds(rr)
    valid = nn_ms[counted]
    pairs = successive_pairs(rr, counted)
    diffs = np.diff(nn_ms)[pairs[1:]]
    bpm = instantaneous_bpm(rr.rr_seconds[counted])
    summary = {
        "beats": float(rr.n_beats),
        "meanNNms": round(float(np.mean(valid)), 3),
        "sdnnMs": round(float(np.std(valid, ddof=1)), 3),
        "meanBpm": round(float(np.mean(bpm)), 3),
        "minBpm": round(float(np.min(bpm)), 3),
        "maxBpm": round(float(np.max(bpm)), 3),
    }
    if diffs.size:
        summary["rmssdMs"] = round(float(np.sqrt(np.mean(diffs**2))), 3)
        summary["pnn50"] = round(float(np.mean(np.abs(diffs) > 50.0)), 6)
    return summary


# ---------------------------------------------------------------------------
# VFC del informe Holter (Task Force ESC/NASPE 1996), sobre NN en ms.
# ---------------------------------------------------------------------------
WINDOW_S = 300.0
#: Una ventana de 5 min entra al espectro si tiene NN que cubran al menos esto.
MIN_WINDOW_COVERAGE = 0.8
RESAMPLE_HZ = 4.0
MIN_ULF_WINDOWS = 6

ULF_BAND = (0.0, 0.0033)
VLF_BAND = (0.0033, 0.04)
LF_BAND = (0.04, 0.15)
HF_BAND = (0.15, 0.4)


def _window_ids(times_s: np.ndarray) -> np.ndarray:
    return np.floor(times_s / WINDOW_S).astype(np.int64)


def _full_windows(nn_ms: np.ndarray, times_s: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Ventanas de 5 min con cobertura suficiente: `(id, posiciones)`."""
    ids = _window_ids(times_s)
    result: list[tuple[int, np.ndarray]] = []
    for window in np.unique(ids):
        positions = np.flatnonzero(ids == window)
        if nn_ms[positions].sum() >= MIN_WINDOW_COVERAGE * WINDOW_S * 1000:
            result.append((int(window), positions))
    return result


def time_domain(
    nn_ms: np.ndarray, times_s: np.ndarray, successive_ms: np.ndarray
) -> dict[str, float | None]:
    """`successive_ms` son las diferencias entre NN adyacentes (mismo latido)."""
    if nn_ms.size < 2:
        return {"sdnnMs": None, "sdannMs": None, "rmssdMs": None, "pnn50Percent": None, "cv": None}
    mean = float(nn_ms.mean())
    sdnn = float(nn_ms.std(ddof=1))
    window_means = [
        float(nn_ms[positions].mean()) for _, positions in _full_windows(nn_ms, times_s)
    ]
    sdann = float(np.std(window_means, ddof=1)) if len(window_means) >= 2 else None
    rmssd = float(np.sqrt(np.mean(successive_ms**2))) if successive_ms.size else None
    pnn50 = float(np.mean(np.abs(successive_ms) > 50) * 100) if successive_ms.size else None
    return {
        "sdnnMs": sdnn,
        "sdannMs": sdann,
        "rmssdMs": rmssd,
        "pnn50Percent": pnn50,
        "cv": sdnn / mean if mean else None,
    }


def _band_power(freqs: np.ndarray, psd: np.ndarray, band: tuple[float, float]) -> float:
    mask = (freqs > band[0]) & (freqs <= band[1])
    if mask.sum() < 2:
        return float(psd[mask].sum() * (freqs[1] - freqs[0])) if mask.any() else 0.0
    return float(np.trapezoid(psd[mask], freqs[mask]))


def frequency_domain(nn_ms: np.ndarray, times_s: np.ndarray) -> dict[str, Any] | None:
    """Potencias en ms² y el espectro promedio (para el gráfico), o `None`."""
    windows = _full_windows(nn_ms, times_s)
    if not windows:
        return None
    spectra: list[np.ndarray] = []
    freqs = np.empty(0)
    grid = np.arange(int(WINDOW_S * RESAMPLE_HZ)) / RESAMPLE_HZ
    for window, positions in windows:
        start = window * WINDOW_S
        times, values = times_s[positions], nn_ms[positions]
        points = np.clip(start + grid, times[0], times[-1])
        if times.size >= 4 and np.all(np.diff(times) > 0):
            resampled = CubicSpline(times, values)(points)
        else:
            resampled = np.interp(points, times, values)
        freqs, psd = periodogram(
            resampled, fs=RESAMPLE_HZ, window="hann", detrend="linear", scaling="density"
        )
        spectra.append(psd)
    mean_psd = np.mean(spectra, axis=0)

    ulf: float | None = None
    ids = np.array([window for window, _ in windows])
    # Un hueco entre ventanas no contiene NN observados. Interpolarlo inventa
    # una tendencia lenta y puede inflar la potencia ULF del informe.
    if len(windows) >= MIN_ULF_WINDOWS and np.all(np.diff(ids) == 1):
        means = np.array([nn_ms[positions].mean() for _, positions in windows])
        ulf_freqs, ulf_psd = periodogram(means, fs=1 / WINDOW_S, detrend="constant")
        ulf = float(ulf_psd[1:].sum() * (ulf_freqs[1] - ulf_freqs[0]))

    vlf = _band_power(freqs, mean_psd, VLF_BAND)
    lf = _band_power(freqs, mean_psd, LF_BAND)
    hf = _band_power(freqs, mean_psd, HF_BAND)
    visible = freqs <= HF_BAND[1]
    return {
        "totalPowerMs2": (ulf or 0.0) + vlf + lf + hf,
        "ulfMs2": ulf,
        "vlfMs2": vlf,
        "lfMs2": lf,
        "hfMs2": hf,
        "lfHfRatio": lf / hf if hf > 0 else None,
        "windows": len(windows),
        "spectrum": {
            "frequenciesHz": freqs[visible].tolist(),
            "powerMs2PerHz": mean_psd[visible].tolist(),
        },
    }
