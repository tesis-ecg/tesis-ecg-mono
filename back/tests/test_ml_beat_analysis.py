"""Detector de picos R y medición de ST sobre ECG sintético con verdad conocida."""

import numpy as np

from app.ml.beats import BEAT_DTYPE, analyze_window, decode_beats, encode_beats
from app.ml.rpeak_detection import detect_r_peaks
from app.ml.st_analysis import measure_st_levels
from tests.synthetic_ecg import beat_times, synthetic_ecg

RATE = 500
TOLERANCE = round(0.010 * RATE)


def _variable_rr(count: int) -> np.ndarray:
    rng = np.random.default_rng(3)
    rr = 0.8 + 0.25 * np.sin(np.arange(count) / 15) + 0.03 * rng.standard_normal(count)
    rr[count // 2] = 2.4  # una pausa
    return rr


def _score(peaks: np.ndarray, truth_s: np.ndarray) -> tuple[float, float, int]:
    truth = np.round(truth_s * RATE).astype(np.int64)
    distance = np.abs(peaks[:, None] - truth[None, :])
    matched_peaks = distance.min(axis=1) <= TOLERANCE
    matched_truth = distance.min(axis=0) <= TOLERANCE
    return matched_truth.mean(), matched_peaks.mean(), int(distance.min(axis=1).max())


def test_detecta_todos_los_latidos_con_fc_variable_y_una_pausa() -> None:
    times = beat_times(_variable_rr(400))
    signal = synthetic_ecg(times, times[-1] + 1.5, RATE)

    sensitivity, ppv, worst = _score(detect_r_peaks(signal, RATE), times)

    assert sensitivity >= 0.99 and ppv >= 0.99
    assert worst <= TOLERANCE


def test_tolera_ruido_deriva_red_y_continua_del_electrodo() -> None:
    times = beat_times(_variable_rr(400))
    signal = synthetic_ecg(times, times[-1] + 1.5, RATE, noise_mv=0.1, wander_mv=1.0, mains_mv=0.5)
    sensitivity, ppv, _ = _score(detect_r_peaks(signal + 50.0, RATE), times)

    assert sensitivity >= 0.99 and ppv >= 0.99


def test_senal_plana_o_corta_no_inventa_latidos() -> None:
    assert detect_r_peaks(np.zeros(10 * RATE, dtype=np.float32), RATE).size == 0
    assert detect_r_peaks(np.zeros(RATE, dtype=np.float32), RATE).size == 0


def test_st_mide_la_elevacion_inyectada() -> None:
    times = beat_times(np.full(200, 0.8))
    st = np.zeros(times.size)
    st[80:160] = 0.2
    signal = synthetic_ecg(times, times[-1] + 1.5, RATE, st_mv=st, noise_mv=0.02)
    peaks = detect_r_peaks(signal, RATE)

    levels = measure_st_levels(signal, peaks, RATE)

    assert abs(float(np.nanmedian(levels[10:70]))) < 0.02
    assert abs(float(np.nanmedian(levels[90:150])) - 0.2) < 0.03


def test_analyze_window_conserva_solo_el_tramo_pedido_en_coordenada_absoluta() -> None:
    times = beat_times(np.full(60, 0.8))
    signal = synthetic_ecg(times, times[-1] + 1.5, RATE)
    offset = 1_000_000

    beats = analyze_window(
        signal, RATE, offset=offset, keep_start=offset + 10 * RATE, keep_end=offset + 30 * RATE
    )

    assert beats.dtype == BEAT_DTYPE
    assert beats["sample_index"].min() >= offset + 10 * RATE
    assert beats["sample_index"].max() < offset + 30 * RATE
    assert beats.size == 25
    assert np.array_equal(decode_beats(encode_beats(beats)), beats)
