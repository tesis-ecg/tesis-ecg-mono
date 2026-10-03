"""Detección de picos R sobre una derivación, del lado del backend.

El equipo ya marca `R_PEAK` por muestra, pero su detector no está validado,
deja de correr con `FILTER_BAD` y no ubica puntos fiduciales. Las métricas del
informe (FC, pausas, VFC, ST) necesitan una posición de R propia y estable, y el
futuro clasificador de latidos va a partir de este mismo paso.

Es un Pan-Tompkins clásico: pasa-banda 5–15 Hz, derivada, cuadrado e
integración en 150 ms, con umbral adaptativo, período refractario, búsqueda
hacia atrás y descarte de ondas T. La posición final se refina sobre la señal
con pasa-banda de 0,5–40 Hz, donde el pico es el del QRS y no el de la energía.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

#: Ningún QRS puede seguir a otro en menos de esto (240 lpm).
REFRACTORY_S = 0.25
#: Dentro de esta ventana un candidato débil suele ser la onda T del latido previo.
T_WAVE_WINDOW_S = 0.36
INTEGRATION_S = 0.15
#: Si pasa 1,66 × el RR medio sin QRS se busca hacia atrás con medio umbral.
SEARCHBACK_FACTOR = 1.66
#: Ventana de refinamiento alrededor del pico de la integración.
REFINE_BEFORE_S = 0.15
REFINE_AFTER_S = 0.10


def _bandpass(signal: np.ndarray, low: float, high: float, rate: int) -> np.ndarray:
    sos = butter(3, (low, high), btype="bandpass", fs=rate, output="sos")
    return np.asarray(sosfiltfilt(sos, signal.astype(np.float64)))


def _integrated_energy(signal: np.ndarray, rate: int) -> np.ndarray:
    band = _bandpass(signal, 5.0, 15.0, rate)
    squared = np.gradient(band) ** 2
    window = max(1, int(INTEGRATION_S * rate))
    return np.convolve(squared, np.ones(window) / window, mode="same")


#: Los filtros de fase cero dejan transitorio en los bordes; ahí no se busca.
EDGE_S = 0.25
#: Ventana inicial con la que se estiman los niveles de señal y de ruido.
LEARNING_S = 8.0


def _classify_candidates(mwi: np.ndarray, candidates: np.ndarray, rate: int) -> list[int]:
    """Umbral adaptativo de Pan-Tompkins sobre los máximos de la integración.

    Los niveles iniciales salen de percentiles de los candidatos de los
    primeros segundos y no del máximo: un solo artefacto (o el transitorio de
    borde del filtro) dejaría el umbral por encima de todos los latidos.
    """
    learning = mwi[candidates[candidates < LEARNING_S * rate]]
    if learning.size < 2:
        learning = mwi[candidates]
    spki = float(np.percentile(learning, 75))
    npki = float(np.percentile(learning, 25))
    refractory = int(REFRACTORY_S * rate)
    t_window = int(T_WAVE_WINDOW_S * rate)

    qrs: list[int] = []
    pending: list[int] = []  # candidatos descartados desde el último QRS
    rr_recent: list[int] = []

    def accept(index: int, weight: float) -> None:
        nonlocal spki
        spki = weight * float(mwi[index]) + (1 - weight) * spki
        if qrs:
            rr_recent.append(index - qrs[-1])
            del rr_recent[:-8]
        qrs.append(index)
        pending[:] = [item for item in pending if item > index]

    for candidate in candidates:
        threshold = npki + 0.25 * (spki - npki)
        last = qrs[-1] if qrs else int(candidates[0]) - refractory
        rr_mean = sum(rr_recent) / len(rr_recent) if rr_recent else float(rate)
        if candidate - last > SEARCHBACK_FACTOR * rr_mean:
            missed = [
                index
                for index in pending
                if index - last >= refractory
                and candidate - index >= refractory
                and mwi[index] > threshold / 2
            ]
            if missed:
                accept(max(missed, key=lambda index: mwi[index]), 0.25)
        value = float(mwi[candidate])
        if qrs and candidate - qrs[-1] < refractory:
            continue
        is_t_wave = bool(qrs) and candidate - qrs[-1] < t_window and value < 0.5 * mwi[qrs[-1]]
        if value > threshold and not is_t_wave:
            accept(int(candidate), 0.125)
        else:
            npki = 0.125 * value + 0.875 * npki
            pending.append(int(candidate))
    return qrs


def detect_r_peaks(signal_mv: np.ndarray, rate: int) -> np.ndarray:
    """Índices (int64, crecientes) de los picos R de `signal_mv`."""
    if signal_mv.size < 2 * rate:
        return np.empty(0, dtype=np.int64)
    mwi = _integrated_energy(signal_mv, rate)
    if not np.any(mwi > 0):
        return np.empty(0, dtype=np.int64)
    candidates, _ = find_peaks(mwi, distance=max(1, int(0.2 * rate)))
    edge = int(EDGE_S * rate)
    candidates = candidates[(candidates >= edge) & (candidates < mwi.size - edge)]
    if candidates.size == 0:
        return np.empty(0, dtype=np.int64)
    qrs = sorted(_classify_candidates(mwi, candidates, rate))
    if not qrs:
        return np.empty(0, dtype=np.int64)

    shaped = _bandpass(signal_mv, 0.5, 40.0, rate)
    before = int(REFINE_BEFORE_S * rate)
    after = int(REFINE_AFTER_S * rate)
    refined: list[int] = []
    min_distance = int(0.2 * rate)
    for index in qrs:
        start = max(0, index - before)
        stop = min(shaped.size, index + after + 1)
        peak = start + int(np.argmax(np.abs(shaped[start:stop])))
        # Un máximo pegado al borde de la ventana no es el R sino la pendiente
        # de otra cosa (típicamente el transitorio del filtro al inicio de un
        # tramo): ahí vale más la posición de la integración.
        if peak in (start, stop - 1):
            peak = index
        if refined and peak - refined[-1] < min_distance:
            continue
        refined.append(peak)
    return np.asarray(refined, dtype=np.int64)
