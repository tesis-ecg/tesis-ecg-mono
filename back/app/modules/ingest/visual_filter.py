"""Zero-phase ECG view. The original decoded samples and raw frames stay intact.

Each committed piece is filtered with two minutes of continuous ECG on either
side. The unfinished tail of the active run waits for the next upload. A run
that ended at a gap or study close can use its real boundary immediately.
"""

from __future__ import annotations

from typing import cast

import numpy as np
from scipy.signal import butter, filtfilt, iirnotch, sosfiltfilt

CONTEXT_SECONDS = 120


def filter_visualization(signal_mv: np.ndarray, sample_rate: int) -> np.ndarray:
    """50 Hz notch and 0.05–40 Hz band-pass, forward and backward.

    The 40 Hz ceiling deliberately makes this a display-only view: it must not
    feed QRS amplitude measurements or a future diagnostic classifier. The ST
    level (`app/ml/st_analysis.py`) is the one measurement it does feed: ST is a
    low-frequency feature, and the 0.05 Hz high-pass is the AHA recommendation
    for preserving it.
    """
    if signal_mv.size == 0:
        return np.empty(0, dtype="<f4")
    if signal_mv.size < 32:
        padded = np.pad(signal_mv, (32, 32), mode="edge")
        return filter_visualization(padded, sample_rate)[32:-32]
    notch_b, notch_a = iirnotch(50.0, 30.0, fs=sample_rate)
    band = butter(4, (0.05, 40.0), btype="bandpass", fs=sample_rate, output="sos")
    without_mains = filtfilt(notch_b, notch_a, signal_mv.astype(np.float64))
    return cast(np.ndarray, sosfiltfilt(band, without_mains).astype("<f4"))
