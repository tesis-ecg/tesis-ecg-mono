"""Nivel del segmento ST por latido.

Se mide sobre la señal con pasa-banda de 0,05–40 Hz de fase cero y notch de
50 Hz (`filter_visualization`): el pasa-altos de 0,05 Hz es el que recomienda
la AHA justamente para no deformar el ST. Sin detección de fin de QRS, el punto
ST se toma a una distancia fija del R —J ≈ R + 40 ms, ST = J + 60 ms— y se
acorta con frecuencias altas, donde el ST queda más cerca del QRS.
"""

from __future__ import annotations

import numpy as np

from app.modules.ingest.visual_filter import filter_visualization

ISOELECTRIC_START_S = -0.080
ISOELECTRIC_END_S = -0.060
ST_OFFSET_S = 0.100
ST_OFFSET_FAST_S = 0.080
#: Por debajo de este RR (120 lpm) se usa el punto ST corto.
FAST_RR_S = 0.5
ST_HALF_WINDOW_S = 0.010


def measure_st_levels(
    signal_mv: np.ndarray,
    peaks: np.ndarray,
    rate: int,
    *,
    filtered: np.ndarray | None = None,
) -> np.ndarray:
    """Nivel ST en mV (float32) por pico; NaN si la ventana cae fuera de la señal."""
    levels = np.full(peaks.size, np.nan, dtype=np.float32)
    if peaks.size == 0:
        return levels
    shaped = filter_visualization(signal_mv, rate) if filtered is None else filtered
    iso_start = round(ISOELECTRIC_START_S * rate)
    iso_end = round(ISOELECTRIC_END_S * rate)
    half = max(1, round(ST_HALF_WINDOW_S * rate))
    for position, peak in enumerate(peaks):
        rr = (peak - peaks[position - 1]) / rate if position > 0 else None
        offset = ST_OFFSET_FAST_S if rr is not None and rr < FAST_RR_S else ST_OFFSET_S
        st_center = int(peak) + round(offset * rate)
        iso_lo, iso_hi = int(peak) + iso_start, int(peak) + iso_end
        if iso_lo < 0 or st_center + half >= shaped.size:
            continue
        baseline = float(np.median(shaped[iso_lo : iso_hi + 1]))
        level = float(np.mean(shaped[st_center - half : st_center + half + 1]))
        levels[position] = level - baseline
    return levels
