"""Etapa 1 — el gate de calidad. Decide qué tramo del registro es analizable.

Es lo primero que corre y lo que más impacto tiene: un artefacto de movimiento
se parece muchísimo más a una arritmia que a un latido normal, así que un motor
que no separa ruido primero produce cientos de falsos positivos por día y el
médico deja de mirar la herramienta.

Tres capas, y **ninguna manda sola**:

- **Capa A — el hardware.** Bits de `LEAD_OFF` y `ADC_SATURATED` que el AFE
  reporta por muestra. Determinística y sin discusión: si el electrodo está
  despegado no hay señal, no importa qué diga ningún índice espectral.
- **Capa B — índices de señal.** pSQI, kSQI y basSQI calculados acá con scipy.
- **Capa B' — el acuerdo entre detectores (bSQI).** Ver `rpeak_detection.py`.

La regla de combinación es **conservadora por diseño**: la ventana es `good`
solo si ninguna capa objeta. Alcanza con que una la degrade.

## Por qué los SQIs se calculan acá y no con `nk.ecg_quality`

Se midió, y hay tres razones concretas:

1. `method="zhao2018"` devuelve **un string para toda la señal que recibe**, no
   una serie. Para tener un valor por ventana de 10 s habría que llamarlo 360
   veces por lote, y cada llamada vuelve a correr `ecg_peaks` internamente.
2. Su implementación **descartó el índice qSQI** del paper original y
   redistribuyó los pesos. No es el ensemble que la cita promete.
3. Falla en las dos direcciones: le da `Barely acceptable` a un flatline —el
   modo de falla más obvio de un electrodo seco— y `Excellent` a ruido gaussiano
   puro sin un solo QRS.

Son tres fórmulas cerradas de treinta líneas de scipy, testeables con señales
sintéticas. Calcularlas acá deja la superficie de neurokit2 en exactamente dos
funciones (`ecg_clean` y `ecg_peaks`), que es lo que hace que la dependencia sea
honesta y no decorativa.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from app.db.models.signal_quality import SignalQualityLevel
from app.ml.contracts import (
    Flags,
    Floats,
    Indices,
    Mask,
    QualityReason,
    QualityReport,
    QualityThresholds,
    QualityWindow,
    Signal,
)
from app.ml.decompression import (
    FLAG_ADC_SATURATED,
    FLAG_LEAD_OFF,
    FLAG_RLD_OFF,
    FLAG_SQI_MASK,
    FLAG_SQI_SHIFT,
    SQ_BAD,
)
from app.ml.rpeak_detection import beat_sqi

#: Fracción de la ventana que tiene que estar afectada para que un bit del
#: hardware la invalide. Un electrodo que rebota una muestra no arruina 10 s de
#: registro; uno despegado marca la ventana entera.
VETO_FRACTION = 0.05

#: Bandas de los índices espectrales (Zhao & Zhang, 2018).
QRS_BAND = (5.0, 15.0)
SIGNAL_BAND = (5.0, 40.0)
FULL_BAND = (0.0, 40.0)

#: Banda de deriva de línea de base: **0-0,5 Hz y no los 0-1 Hz del paper**.
#:
#: Medido: a 60 lpm el fundamental cardíaco cae justo en 1 Hz, así que con la
#: banda del paper el propio ritmo del paciente cuenta como "deriva" y un ECG
#: perfectamente limpio da basSQI = 0,907 — apenas por encima del umbral de 0,90,
#: y a 100 lpm la cosa empeora. Con 0-0,5 Hz el mismo ECG da 0,987 y uno con
#: deriva real de 0,15 Hz da 0,795: la separación pasa de nula a un factor claro.
BASELINE_BAND = (0.0, 0.5)


def sample_runs(mask: Mask) -> list[tuple[int, int]]:
    """Tramos `(inicio, largo)` donde `mask` es verdadera."""
    if mask.size == 0 or not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(s), int(e - s)) for s, e in zip(edges[0::2], edges[1::2], strict=True)]


def window_bounds(n_samples: int, window_samples: int) -> list[tuple[int, int]]:
    """Ventanas `(inicio, largo)` que cubren la señal **entera**, sin huecos.

    La última absorbe el remanente en vez de descartarlo. Que quede más larga que
    las otras es preferible a dejar hasta 10 s del registro sin evaluar: un tramo
    sin nivel de calidad no se distingue de uno bueno cuando el visor lo dibuja.
    """
    if n_samples <= 0:
        return []
    if n_samples <= window_samples:
        return [(0, n_samples)]
    count = n_samples // window_samples
    bounds = [(index * window_samples, window_samples) for index in range(count - 1)]
    last_start = (count - 1) * window_samples
    bounds.append((last_start, n_samples - last_start))
    return bounds


# --------------------------------------------------------------------------- #
# Capa A — bits del hardware
# --------------------------------------------------------------------------- #


def hardware_veto(window_flags: Flags) -> QualityReason | None:
    """Qué bit del AFE invalida esta ventana, si alguno.

    `RLD_OFF` **no** invalida: degrada el rechazo de modo común, pero el par
    RA-LL sigue midiendo una diferencia de potencial real (`INTEGRACION.md` §4.5,
    regla 2). Sí exime a la saturación, porque sin pierna derecha la señal se va
    al riel por deriva de modo común y no por un artefacto del paciente.
    """
    if window_flags.size == 0:
        return None
    threshold = max(1, int(window_flags.size * VETO_FRACTION))
    if int(np.count_nonzero(window_flags & FLAG_LEAD_OFF)) >= threshold:
        return "lead_off"
    saturated = (window_flags & FLAG_ADC_SATURATED) != 0
    rld_ok = (window_flags & FLAG_RLD_OFF) == 0
    if int(np.count_nonzero(saturated & rld_ok)) >= threshold:
        return "saturated"
    # El SQI que ya calculó el firmware. No reemplaza a la Capa B —es un umbral
    # de amplitud sobre el MCU— pero cuando dice "inutilizable" durante media
    # ventana, coincide.
    unanalyzable = ((window_flags & FLAG_SQI_MASK) >> FLAG_SQI_SHIFT) == SQ_BAD
    if int(np.count_nonzero(unanalyzable)) >= window_flags.size // 2:
        return "firmware_sqi"
    return None


def is_flatline(window: Signal, flatline_mv: float) -> bool:
    """Amplitud pico a pico robusta por debajo del umbral.

    Percentiles 5-95 y no min-max: un solo spike de conmutación levantaría el
    rango de una línea perfectamente plana y la haría pasar por señal.
    """
    if window.size < 2:
        return True
    low, high = np.percentile(window.astype(np.float64), [5.0, 95.0])
    return bool(high - low < flatline_mv)


# --------------------------------------------------------------------------- #
# Capa B — índices espectrales
# --------------------------------------------------------------------------- #


def _band_power(freqs: Floats, psd: Floats, band: tuple[float, float]) -> float:
    selected = (freqs >= band[0]) & (freqs <= band[1])
    if not selected.any():
        return 0.0
    return float(np.trapezoid(psd[selected], freqs[selected]))


def spectral_sqi(window: Signal, sample_rate: int) -> tuple[float, float, float]:
    """`(pSQI, kSQI, basSQI)` de una ventana.

    **Sobre la señal cruda, nunca sobre la limpia.** No es un detalle: se midió
    ruido gaussiano puro, sin un solo QRS, por los dos caminos.

    | Señal                | pSQI  | kSQI  | basSQI |
    |----------------------|-------|-------|--------|
    | ruido puro, cruda    | 0,316 |  3,07 | 0,996  |
    | ruido puro, filtrada | 0,651 | 12,48 | 0,858  |
    | ECG limpio, cruda    | 0,677 | 15,42 | 0,987  |

    Filtrado, el ruido blanco **pasa los tres umbrales**: `ecg_clean` lo recorta a
    la banda del QRS y lo deja pareciéndose a un ECG. Es la explicación mecánica
    del falso-pase de `nk.ecg_quality(method="zhao2018")`, que trabaja sobre la
    señal filtrada. Sobre la cruda, la curtosis de 3,07 —la de una gaussiana— lo
    delata sin ambigüedad.

    Y el basSQI solo tiene sentido crudo por definición: mide cuánta energía se
    fue a la deriva de línea de base, y el pasa-altos ya la eliminó (medido: 0,879
    sin deriva contra 0,880 con deriva fuerte, o sea que no discrimina nada).
    """
    from scipy import signal as sp_signal
    from scipy import stats as sp_stats

    if window.size < sample_rate // 2:
        return 0.0, 0.0, 0.0
    data = window.astype(np.float64)
    nperseg = min(data.size, 1024)
    raw_freqs, raw_psd = sp_signal.welch(data, fs=float(sample_rate), nperseg=nperseg)
    freqs = np.asarray(raw_freqs, dtype=np.float32)
    psd = np.asarray(raw_psd, dtype=np.float32)

    signal_power = _band_power(freqs, psd, SIGNAL_BAND)
    psqi = _band_power(freqs, psd, QRS_BAND) / signal_power if signal_power > 0 else 0.0

    full_power = _band_power(freqs, psd, FULL_BAND)
    bassqi = 1.0 - _band_power(freqs, psd, BASELINE_BAND) / full_power if full_power > 0 else 0.0

    # Curtosis de Pearson (normal = 3). Un QRS es un pico raro y angosto sobre
    # una línea de base: la distribución de amplitudes queda muy leptocúrtica.
    # El ruido gaussiano da ~3 y por ahí se lo atrapa.
    ksqi = float(np.asarray(sp_stats.kurtosis(data, fisher=False)))
    if not np.isfinite(ksqi):
        ksqi = 0.0
    return psqi, ksqi, bassqi


# --------------------------------------------------------------------------- #
# Combinación
# --------------------------------------------------------------------------- #


def assess_quality(
    signal: Signal,
    flags: Flags,
    cleaned: Signal,
    firmware_peaks: Indices,
    detected_peaks: Indices,
    *,
    sample_rate: int,
    thresholds: QualityThresholds,
) -> QualityReport:
    """Evalúa el lote ventana por ventana y devuelve la máscara de analizable.

    `analyzable` es verdadera solo donde el nivel es `good`: la Etapa 2 compara
    formas de onda, y una ventana `marginal` sirve para contar latidos pero no
    para afirmar que uno tiene una morfología distinta.
    """
    n_samples = int(signal.size)
    analyzable = np.zeros(n_samples, dtype=bool)
    firmware_available = firmware_peaks.size > 0
    windows: list[QualityWindow] = []

    for start, length in window_bounds(n_samples, thresholds.window_samples):
        end = start + length
        level, reason, metrics = _assess_window(
            signal[start:end],
            flags[start:end],
            cleaned[start:end] if cleaned.size == n_samples else signal[start:end],
            firmware_peaks[(firmware_peaks >= start) & (firmware_peaks < end)] - start,
            detected_peaks[(detected_peaks >= start) & (detected_peaks < end)] - start,
            sample_rate=sample_rate,
            thresholds=thresholds,
            firmware_available=firmware_available,
        )
        windows.append(
            QualityWindow(
                start_sample=start,
                length_samples=length,
                level=level,
                reason=reason,
                psqi=metrics[0],
                ksqi=metrics[1],
                bassqi=metrics[2],
                bsqi=metrics[3],
            )
        )
        if level is SignalQualityLevel.GOOD:
            analyzable[start:end] = True

    return QualityReport(
        windows=tuple(windows),
        analyzable=analyzable,
        firmware_peaks_available=firmware_available,
    )


def _assess_window(
    window: Signal,
    window_flags: Flags,
    window_cleaned: Signal,
    firmware_peaks: Indices,
    detected_peaks: Indices,
    *,
    sample_rate: int,
    thresholds: QualityThresholds,
    firmware_available: bool,
) -> tuple[SignalQualityLevel, QualityReason, tuple[float | None, ...]]:
    veto = hardware_veto(window_flags)
    if veto is not None:
        # Capa A gana sin calcular nada más: no tiene sentido pedirle un índice
        # espectral a un electrodo despegado.
        return SignalQualityLevel.BAD, veto, (None, None, None, None)

    if is_flatline(window, thresholds.flatline_mv):
        return SignalQualityLevel.BAD, "flatline", (None, None, None, None)

    psqi, ksqi, bassqi = spectral_sqi(window, sample_rate)
    bsqi = (
        beat_sqi(firmware_peaks, detected_peaks, thresholds.bsqi_tolerance_samples)
        if firmware_available
        else None
    )
    metrics: tuple[float | None, ...] = (psqi, ksqi, bassqi, bsqi)

    if psqi < thresholds.psqi_min or ksqi < thresholds.ksqi_min or bassqi < thresholds.bassqi_min:
        return SignalQualityLevel.BAD, "spectral", metrics

    # Ningún detector encontró un latido en 10 s de señal que pasó los índices
    # espectrales. Es el falso-pase que se midió sobre ruido gaussiano puro.
    if detected_peaks.size == 0 and firmware_peaks.size == 0:
        return SignalQualityLevel.BAD, "no_beats", metrics

    if bsqi is not None and bsqi < thresholds.bsqi_min:
        # Los dos detectores ven latidos pero no los mismos. Hay señal —se puede
        # contar y medir R-R— pero comparar morfologías sobre esto produciría
        # anomalías que son artefactos de alineación.
        return SignalQualityLevel.MARGINAL, "bsqi", metrics

    return SignalQualityLevel.GOOD, "ok", metrics


def _median_or_none(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(float(np.median(present)), 6)


def merge_windows(windows: Sequence[QualityWindow]) -> list[QualityWindow]:
    """Colapsa ventanas contiguas con el mismo `(nivel, motivo)` en intervalos.

    Un registro limpio de una hora pasa de 360 filas a una sola. El caso
    patológico —alternancia perfecta— no colapsa nada y deja 360, que sigue
    siendo un techo conocido y acotado.
    """
    merged: list[QualityWindow] = []
    buffer: list[QualityWindow] = []

    def flush() -> None:
        if not buffer:
            return
        first = buffer[0]
        last = buffer[-1]
        merged.append(
            QualityWindow(
                start_sample=first.start_sample,
                length_samples=last.start_sample + last.length_samples - first.start_sample,
                level=first.level,
                reason=first.reason,
                psqi=_median_or_none([item.psqi for item in buffer]),
                ksqi=_median_or_none([item.ksqi for item in buffer]),
                bassqi=_median_or_none([item.bassqi for item in buffer]),
                bsqi=_median_or_none([item.bsqi for item in buffer]),
            )
        )
        buffer.clear()

    for window in windows:
        if buffer and (buffer[-1].level is not window.level or buffer[-1].reason != window.reason):
            flush()
        buffer.append(window)
    flush()
    return merged


def window_counts(windows: Sequence[QualityWindow], merged: QualityWindow) -> int:
    """Cuántas ventanas originales entraron en un intervalo fusionado."""
    end = merged.start_sample + merged.length_samples
    return sum(1 for item in windows if merged.start_sample <= item.start_sample < end)
