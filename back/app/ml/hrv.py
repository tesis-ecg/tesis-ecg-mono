"""Variabilidad de la frecuencia cardíaca sobre intervalos NN.

Sigue las definiciones del Task Force de la ESC/NASPE (1996):

- **Tiempo:** SDNN, SDANN (desvío de las medias de 5 min), rMSSD, pNN50 y el
  coeficiente de variación CV = SDNN / NN medio.
- **Frecuencia:** VLF, LF y HF salen del promedio de los espectros de ventanas de
  5 min. Cada ventana se remuestrea a 4 Hz con spline cúbica, se le quita la tendencia lineal y se
  le aplica una ventana de Hann. ULF sale del espectro de la serie de medias de
  5 min cuando son consecutivas: con un punto cada 300 s, esa serie cae entera
  en la banda < 0,0033 Hz. Si hay ventanas sin señal, ULF queda sin calcular.
  La energía total suma las bandas disponibles, como en los informes comerciales.

Todas las funciones son puras; reciben NN en ms y su instante en s.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import periodogram

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
