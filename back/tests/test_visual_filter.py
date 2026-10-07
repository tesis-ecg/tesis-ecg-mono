"""Vista filtrada para visualización (`visual_filter.py`) y el ST que no debe cambiar.

La respuesta se mide como la mide la herramienta del chaleco (`visor_backend.py
filtros`): un impulso en el medio de 2^17 muestras y el módulo de la FFT de la
salida. Con un filtro de fase cero ese módulo es la respuesta entera.
"""

from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, filtfilt, iirnotch, sosfiltfilt

from app.ml.beats import analyze_window
from app.ml.rpeak_detection import detect_r_peaks
from app.ml.st_analysis import measure_st_levels
from app.modules.ingest.visual_filter import (
    CONTEXT_SECONDS,
    filter_band_notch,
    filter_visualization,
)
from tests.synthetic_ecg import beat_times, synthetic_ecg

RATE = 500
#: Entre 45 y 55 Hz la cadena de a bordo del chaleco atenúa 47-95 dB
#: (`INTEGRACION.md` §14.6); la vista tiene que atenuar por lo menos eso. El
#: diseño da 111,8 dB en el peor punto (45,1 Hz).
MAINS_REJECTION_DB = 100.0
#: Cuánto puede moverse la banda hasta 40 Hz respecto de la receta §6.2 sola.
#: El diseño la mueve 0,0001 dB.
PASSBAND_TOLERANCE_DB = 0.001


def _recipe_6_2(signal_mv: np.ndarray, fs: int) -> np.ndarray:
    """La vista tal como era antes del pasa-bajos, congelada: el ST se mide sobre esto."""
    if signal_mv.size == 0:
        return np.empty(0, dtype="<f4")
    if signal_mv.size < 32:
        return _recipe_6_2(np.pad(signal_mv, (32, 32), mode="edge"), fs)[32:-32]
    notch_b, notch_a = iirnotch(50.0, 30.0, fs=fs)
    band = butter(4, (0.05, 40.0), btype="bandpass", fs=fs, output="sos")
    return sosfiltfilt(band, filtfilt(notch_b, notch_a, signal_mv.astype(np.float64))).astype("<f4")


def _spectrum(filter_fn) -> tuple[np.ndarray, np.ndarray]:
    size = 1 << 17
    impulse = np.zeros(size, dtype="<f4")
    impulse[size // 2] = 1.0
    output = np.asarray(filter_fn(impulse, RATE), dtype=np.float64)
    return np.fft.rfftfreq(size, 1 / RATE), np.abs(np.fft.rfft(output))


@pytest.fixture(scope="module")
def shown() -> tuple[np.ndarray, np.ndarray]:
    return _spectrum(filter_visualization)


@pytest.fixture(scope="module")
def recipe() -> tuple[np.ndarray, np.ndarray]:
    return _spectrum(filter_band_notch)


def _mains_amplitude(signal_mv: np.ndarray) -> float:
    """Amplitud (mV) del pico más alto entre 45 y 55 Hz, con ventana de Hann."""
    window = np.hanning(signal_mv.size)
    spectrum = np.abs(np.fft.rfft(signal_mv.astype(np.float64) * window))
    freqs = np.fft.rfftfreq(signal_mv.size, 1 / RATE)
    return float(2 * spectrum[(freqs >= 45) & (freqs <= 55)].max() / window.sum())


@pytest.mark.parametrize("hz", [45.0, 48.0, 49.5, 50.0, 50.1, 50.5, 52.0, 55.0])
def test_rechaza_la_red_y_sus_bandas_laterales(shown, hz: float) -> None:
    # 50,1 Hz es donde cae la red en la grilla de muestras (el ADS1292R convierte a
    # ~498,85 SPS); el notch de Q = 30 solo la alcanzaba en 50,0 Hz justos.
    freqs, magnitude = shown
    assert 20 * np.log10(np.interp(hz, freqs, magnitude)) <= -MAINS_REJECTION_DB


def test_rechaza_todo_entre_45_y_55_hz(shown) -> None:
    freqs, magnitude = shown
    band = (freqs >= 45.0) & (freqs <= 55.0)
    assert 20 * np.log10(magnitude[band].max()) <= -MAINS_REJECTION_DB


def test_la_banda_hasta_40_hz_queda_como_estaba(shown, recipe) -> None:
    freqs, magnitude = shown
    _, before = recipe
    band = (freqs >= 0.05) & (freqs <= 40.0)
    change_db = 20 * np.log10(magnitude[band] / before[band])
    assert np.max(np.abs(change_db)) <= PASSBAND_TOLERANCE_DB


def test_fase_cero_un_pulso_simetrico_sigue_simetrico_y_en_su_lugar() -> None:
    # Un QRS de unos 50 ms, con el contexto real a cada lado.
    size = 2 * CONTEXT_SECONDS * RATE + 1
    center = size // 2
    t = (np.arange(size) - center) / RATE
    pulse = np.exp(-0.5 * (t / 0.012) ** 2).astype("<f4")

    output = filter_visualization(pulse, RATE).astype(np.float64)

    assert int(np.argmax(output)) == center
    lags = np.arange(1, 2 * RATE)
    assert np.max(np.abs(output[center + lags] - output[center - lags])) < 1e-6 * output[center]


def test_los_bordes_de_una_corrida_no_caen_ni_oscilan() -> None:
    # Continua de 300 mV, deriva y un ritmo lento que pasan intactos: en el borde el
    # pasa-bajos no tiene que cambiar nada. Rellenando con ceros en vez del reflejo
    # impar, la primera muestra caería 0,2 mV.
    t = np.arange(20 * RATE) / RATE
    signal = (
        300.0 + np.sin(2 * np.pi * 1.2 * t + 0.7) + 0.3 * np.sin(2 * np.pi * 0.2 * t + 0.3)
    ).astype("<f4")

    output = filter_visualization(signal, RATE)
    before = filter_band_notch(signal, RATE)

    assert np.max(np.abs(output[:RATE] - before[:RATE])) < 0.001
    assert np.max(np.abs(output[-RATE:] - before[-RATE:])) < 0.001


@pytest.mark.parametrize("phase", [0.0, 1.0, 2.5])
def test_la_red_no_reaparece_en_los_bordes_de_una_corrida(phase: float) -> None:
    t = np.arange(20 * RATE) / RATE
    mains = (0.5 * np.sin(2 * np.pi * 50.12 * t + phase)).astype("<f4")

    output = filter_visualization(mains, RATE)

    assert _mains_amplitude(output[:RATE]) < 0.5 * 10 ** (-60 / 20)
    assert _mains_amplitude(output[-RATE:]) < 0.5 * 10 ** (-60 / 20)


def test_bloques_con_su_contexto_empalman_con_la_red_y_sus_bandas_laterales() -> None:
    rng = np.random.default_rng(3)
    t = np.arange(600 * RATE) / RATE
    envelope = 1 + 0.5 * np.sin(2 * np.pi * 0.7 * t)
    raw = (
        300.0
        + np.sin(2 * np.pi * 1.1 * t)
        + 0.2 * np.sin(2 * np.pi * 0.2 * t)
        + 2.0 * envelope * np.sin(2 * np.pi * 50.12 * t)
        + 0.02 * rng.standard_normal(t.size)
    ).astype("<f4")
    boundary = 300 * RATE
    context = CONTEXT_SECONDS * RATE

    whole = filter_visualization(raw, RATE)
    left = filter_visualization(raw[: boundary + context], RATE)[:boundary]
    right = filter_visualization(raw[boundary - context :], RATE)[context:]

    assert np.max(np.abs(np.concatenate((left, right)) - whole)) < 1e-5
    assert _mains_amplitude(whole[200 * RATE : 400 * RATE]) < 3.0 * 10 ** (-100 / 20)


@pytest.mark.parametrize("size", [1, 5, 31, 32, 321, 322, 643, 700])
def test_senales_cortas_conservan_largo_y_tipo(size: int) -> None:
    signal = (2.0 + np.sin(np.arange(size) / 7)).astype("<f4")

    output = filter_visualization(signal, RATE)

    assert output.shape == signal.shape
    assert output.dtype == np.dtype("<f4")
    assert np.all(np.isfinite(output))
    assert filter_visualization(np.empty(0, dtype="<f4"), RATE).size == 0


@pytest.mark.parametrize("size", [5, 31, 32, 100, 200, 270, 321, 322, 700])
def test_una_corrida_corta_es_la_receta_mas_el_pasa_bajos(size: int) -> None:
    # Un frame suelto entre dos huecos se filtra como corrida propia. Con menos de
    # medio FIR (321 muestras) se rellenaba la cruda y no la salida de la receta:
    # el pasa-altos de 0,05 Hz veía otra señal y la línea de base se corría 0,1 a
    # 1,7 mV. El pasa-bajos solo puede sacar lo que la receta deja por encima de
    # 40 Hz, que en este ECG no llega a 0,03 mV ni sobre un QRS.
    times = beat_times(np.full(30, 0.8))
    signal = 300.0 + synthetic_ecg(times, times[-1] + 1.5, RATE, noise_mv=0.05, wander_mv=0.5)

    for start in range(0, 20 * RATE, RATE // 4):
        run = signal[start : start + size]
        change = filter_visualization(run, RATE) - filter_band_notch(run, RATE)
        assert np.max(np.abs(change)) < 0.05, start


@pytest.mark.parametrize("size", [0, 5, 31, 32, 33, 20 * RATE])
def test_la_receta_del_st_no_cambia_ni_un_bit(size: int) -> None:
    rng = np.random.default_rng(size)
    signal = (300.0 + rng.standard_normal(size)).astype("<f4")

    assert np.array_equal(filter_band_notch(signal, RATE), _recipe_6_2(signal, RATE))


def test_el_st_y_los_latidos_siguen_midiendose_sobre_la_receta_sin_cambios() -> None:
    times = beat_times(np.full(120, 0.8))
    st = np.zeros(times.size)
    st[40:80] = 0.2
    t = np.arange(int((times[-1] + 1.5) * RATE)) / RATE
    signal = synthetic_ecg(times, times[-1] + 1.5, RATE, st_mv=st, noise_mv=0.05, wander_mv=0.5) + (
        0.3 * np.sin(2 * np.pi * 50.12 * t)
    ).astype("<f4")
    frozen = _recipe_6_2(signal, RATE)
    peaks = detect_r_peaks(signal, RATE)
    offset = 1_000_000
    keep_start, keep_end = offset + 5 * RATE, offset + 90 * RATE

    beats = analyze_window(signal, RATE, offset=offset, keep_start=keep_start, keep_end=keep_end)

    # La vista sí cambió: si el ST la usara, este test lo vería.
    assert not np.array_equal(filter_visualization(signal, RATE), frozen)
    expected_st = measure_st_levels(signal, peaks, RATE, filtered=frozen)
    np.testing.assert_array_equal(measure_st_levels(signal, peaks, RATE), expected_st)
    keep = (peaks + offset >= keep_start) & (peaks + offset < keep_end)
    np.testing.assert_array_equal(beats["sample_index"], peaks[keep] + offset)
    np.testing.assert_array_equal(beats["st_mv"], expected_st[keep])


def test_ningun_analisis_lee_la_vista_filtrada() -> None:
    # FC, VFC y el motor de detección trabajan sobre la cruda; el ST, sobre
    # `filter_band_notch`. La vista (y su copia en S3) es solo para mirar.
    ml = Path(__file__).resolve().parents[1] / "app" / "ml"
    for source in ml.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "filter_visualization" not in text, source.name
        assert "ecg_filtered_" not in text, source.name
