"""Delineación del QRS (`app/ml/qrs.py`): inicio, fin, ancho y alto por latido."""

import numpy as np
import pytest

from app.ml import qrs
from tests.ecg_synth import SAMPLE_RATE, synth_conduction


def _delineate(signal: np.ndarray, peaks: np.ndarray, good: np.ndarray | None = None):
    analyzable = np.ones(signal.size, dtype=bool) if good is None else good
    return qrs.delineate_qrs(signal, peaks, analyzable, SAMPLE_RATE)


def test_un_qrs_angosto_mide_cerca_de_100_ms_y_uno_ventricular_bastante_mas() -> None:
    beats = [1.0 + k for k in range(20)]
    ecg = synth_conduction(beats, duration_s=22.0, wide=frozenset({5, 6, 7}), noise_uv=4.0)

    result = _delineate(ecg.signal_mv, ecg.rpeaks)
    width = result.width_ms(SAMPLE_RATE)

    assert result.valid.all()
    narrow = np.delete(width, [5, 6, 7])
    assert narrow.min() > 70 and narrow.max() < 125
    assert width[5:8].min() > 135
    assert np.median(width[5:8]) > 1.4 * np.median(narrow)
    # Inicio y fin son relativos al R y el R queda adentro.
    assert (result.onset < 0).all() and (result.offset > 0).all()


def test_la_amplitud_es_pico_a_pico_del_complejo_sin_el_filtro_de_visualizacion() -> None:
    """Q (−0,15), R (1,0) y S (−0,25): el complejo va de ~−0,13 a 1,0 mV."""
    ecg = synth_conduction([1.0 + k for k in range(15)], duration_s=17.0, noise_uv=2.0)

    result = _delineate(ecg.signal_mv, ecg.rpeaks)

    assert result.amplitude_mv == pytest.approx(1.14, abs=0.1)


def test_es_determinista_y_el_ruido_mueve_poco_el_ancho_tipico() -> None:
    """Latido a latido el ruido mueve el ancho decenas de ms (el umbral cae sobre
    una cola casi plana); lo que se informa es la mediana de 30 s, y ésa no."""
    clean = synth_conduction([1.0 + k for k in range(30)], duration_s=32.0, noise_uv=0.0)
    noisy = synth_conduction([1.0 + k for k in range(30)], duration_s=32.0, noise_uv=8.0)

    first = _delineate(noisy.signal_mv, noisy.rpeaks)
    again = _delineate(noisy.signal_mv, noisy.rpeaks)
    reference = _delineate(clean.signal_mv, clean.rpeaks)

    assert np.array_equal(first.onset, again.onset) and np.array_equal(first.offset, again.offset)
    median_gap = abs(
        float(np.median(first.width_ms(SAMPLE_RATE)))
        - float(np.median(reference.width_ms(SAMPLE_RATE)))
    )
    assert median_gap <= 12.0


def test_un_latido_que_toca_senal_mala_no_se_mide() -> None:
    """Un artefacto pegado al QRS lo ensancharía: sin ventana buena entera no hay QRS."""
    ecg = synth_conduction([1.0 + k for k in range(10)], duration_s=12.0)
    good = np.ones(ecg.signal_mv.size, dtype=bool)
    good[int(4.9 * SAMPLE_RATE) : int(5.05 * SAMPLE_RATE)] = False  # a 100 ms del R de 5,0 s

    result = _delineate(ecg.signal_mv, ecg.rpeaks, good)

    assert not result.valid[4]
    assert np.isnan(result.width_ms(SAMPLE_RATE)[4])
    assert np.isnan(result.amplitude_mv[4])
    assert result.valid[[0, 1, 2, 7, 8]].all()


def test_sin_complejo_no_inventa_un_qrs() -> None:
    """Una línea plana con un R "detectado" no tiene QRS: ni ancho ni amplitud."""
    flat = np.zeros(10 * SAMPLE_RATE, dtype=np.float32)

    result = _delineate(flat, np.array([2500], dtype=np.int64))

    assert not result.valid.any()
    assert np.isnan(result.width_ms(SAMPLE_RATE)).all()


def test_bordes_de_la_senal_y_entrada_vacia() -> None:
    ecg = synth_conduction([1.0 + k for k in range(5)], duration_s=7.0)
    edge = np.array([10, ecg.signal_mv.size - 10], dtype=np.int64)

    assert not _delineate(ecg.signal_mv, edge).valid.any()
    assert _delineate(ecg.signal_mv, np.empty(0, dtype=np.int64)).valid.size == 0
    assert qrs.bridged_extent(np.zeros((0, 11), dtype=bool), 5, 2)[2].size == 0
