"""Zero-phase ECG view. The original decoded samples and raw frames stay intact.

Each committed piece is filtered with two minutes of continuous ECG on either
side. The unfinished tail of the active run waits for the next upload. A run
that ended at a gap or study close can use its real boundary immediately. The
context is sized for the slowest stage, the 0.05 Hz high-pass; the display
low-pass only reaches half its length (321 samples, 0.64 s at 500 Hz) to
each side.
"""

from __future__ import annotations

from functools import lru_cache
from typing import cast

import numpy as np
from scipy.signal import butter, filtfilt, firwin, iirnotch, kaiserord, oaconvolve, sosfiltfilt

CONTEXT_SECONDS = 120
#: Display low-pass after the band-pass: flat up to 40 Hz, at least 100 dB down
#: from 45 Hz. The Q = 30 notch only rejects mains right at 50.0 Hz, but the
#: ADS1292R converts at ~498.85 SPS, so mains lands near 50.1 Hz on the sample
#: grid and spreads into sidebands 1-2 Hz wide, where the order-4 Butterworth
#: attenuates only 12-30 dB. With this stage 45-55 Hz goes 110 dB or more down,
#: past the vest's onboard chain (47-95 dB), and the band up to 40 Hz moves less
#: than 0.001 dB.
DISPLAY_PASS_HZ = 40.0
DISPLAY_STOP_HZ = 45.0
DISPLAY_STOP_DB = 100.0


def filter_band_notch(signal_mv: np.ndarray, sample_rate: int) -> np.ndarray:
    """50 Hz notch and 0.05–40 Hz band-pass, forward and backward.

    The recipe of `INTEGRACION.md` §6.2, unchanged. The ST level
    (`app/ml/st_analysis.py`) is measured on this and not on the display view:
    ST is a low-frequency feature, and the 0.05 Hz high-pass is the AHA
    recommendation for preserving it. Any change here changes stored ST numbers.
    """
    if signal_mv.size == 0:
        return np.empty(0, dtype="<f4")
    return _band_notch(signal_mv, sample_rate).astype("<f4")


def filter_visualization(signal_mv: np.ndarray, sample_rate: int) -> np.ndarray:
    """`filter_band_notch` plus the display low-pass, all of it zero-phase.

    The 40 Hz ceiling deliberately makes this a display-only view: it must not
    feed QRS amplitude measurements or a future diagnostic classifier.

    The low-pass is a symmetric FIR convolved centered, so it adds no delay and
    no phase. At a run edge the recipe's output is extended with an odd
    reflection over half the FIR, as `filtfilt` pads its own edges, so the first
    and last samples of a run neither droop nor ring. A run shorter than half
    the FIR (321 samples at 500 Hz, e.g. a lone frame between two gaps) gets the
    reflection repeated, which `np.pad` does by itself, so it also keeps the
    recipe under 40 Hz. Padding the raw signal instead would change what the
    0.05 Hz high-pass sees and shift the baseline of such a run by over 1 mV.
    """
    if signal_mv.size == 0:
        return np.empty(0, dtype="<f4")
    taps = _display_lowpass(sample_rate)
    half = taps.size // 2
    shaped = np.pad(_band_notch(signal_mv, sample_rate), half, mode="reflect", reflect_type="odd")
    return cast(np.ndarray, oaconvolve(shaped, taps, mode="valid").astype("<f4"))


def _band_notch(signal_mv: np.ndarray, sample_rate: int) -> np.ndarray:
    """`filter_band_notch` in float64, before the cast.

    Under 32 samples there is no room for `filtfilt`'s own edge padding, so the
    edges are extended first.
    """
    if signal_mv.size < 32:
        padded = np.pad(signal_mv, (32, 32), mode="edge")
        return _band_notch(padded, sample_rate)[32:-32]
    notch_b, notch_a = iirnotch(50.0, 30.0, fs=sample_rate)
    band = butter(4, (0.05, 40.0), btype="bandpass", fs=sample_rate, output="sos")
    without_mains = filtfilt(notch_b, notch_a, signal_mv.astype(np.float64))
    return cast(np.ndarray, sosfiltfilt(band, without_mains))


@lru_cache(maxsize=4)
def _display_lowpass(sample_rate: float) -> np.ndarray:
    """Kaiser-window FIR (643 taps at 500 Hz): odd length, so it centers exactly."""
    width = (DISPLAY_STOP_HZ - DISPLAY_PASS_HZ) / (sample_rate / 2)
    numtaps, beta = kaiserord(DISPLAY_STOP_DB, width)
    taps = firwin(
        numtaps | 1,
        (DISPLAY_PASS_HZ + DISPLAY_STOP_HZ) / 2,
        window=("kaiser", beta),
        fs=sample_rate,
    )
    taps.setflags(write=False)
    return cast(np.ndarray, taps)
