"""ECG sintético con latidos en posiciones conocidas, para los tests de `app/ml`."""

from __future__ import annotations

import numpy as np

#: (desplazamiento s respecto del R, amplitud mV, ancho s) de cada onda.
_WAVES = (
    (-0.20, 0.12, 0.025),  # P
    (-0.03, -0.10, 0.008),  # Q
    (0.00, 1.20, 0.010),  # R
    (0.03, -0.25, 0.008),  # S
    (0.28, 0.30, 0.040),  # T
)


def synthetic_ecg(
    beat_times_s: np.ndarray,
    duration_s: float,
    rate: int = 500,
    *,
    noise_mv: float = 0.0,
    wander_mv: float = 0.0,
    mains_mv: float = 0.0,
    st_mv: np.ndarray | None = None,
    seed: int = 7,
) -> np.ndarray:
    """Señal en mV con un P-QRS-T por cada `beat_times_s`.

    `st_mv` (uno por latido) desplaza el tramo entre el fin del QRS y el fin de
    la T, que es lo que el analizador de ST tiene que medir.
    """
    t = np.arange(int(duration_s * rate)) / rate
    signal = np.zeros_like(t)
    for index, beat in enumerate(beat_times_s):
        lo = np.searchsorted(t, beat - 0.4)
        hi = np.searchsorted(t, beat + 0.6)
        local = t[lo:hi] - beat
        for offset, amplitude, width in _WAVES:
            signal[lo:hi] += amplitude * np.exp(-((local - offset) ** 2) / (2 * width**2))
        if st_mv is not None and st_mv[index]:
            plateau = (local > 0.05) & (local < 0.36)
            ramp = np.clip((local - 0.04) / 0.02, 0, 1) * np.clip((0.38 - local) / 0.02, 0, 1)
            signal[lo:hi] += st_mv[index] * np.where(plateau, 1.0, ramp)
    rng = np.random.default_rng(seed)
    signal += noise_mv * rng.standard_normal(t.size)
    signal += wander_mv * np.sin(2 * np.pi * 0.2 * t)
    signal += mains_mv * np.sin(2 * np.pi * 50 * t)
    return signal.astype(np.float32)


def beat_times(rr_s: np.ndarray, start_s: float = 1.0) -> np.ndarray:
    return start_s + np.concatenate(([0.0], np.cumsum(rr_s)[:-1]))
