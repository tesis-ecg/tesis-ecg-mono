"""Etapa 1 — el gate de calidad.

Lo que estos tests fijan no son umbrales sino **modos de falla**. Cada uno
corresponde a una forma concreta en que un Holter con electrodos secos se
degrada, y a un falso-pase medido de las alternativas de biblioteca.
"""

import numpy as np
import pytest

from app.db.models.signal_quality import SignalQualityLevel
from app.ml.contracts import QualityThresholds
from app.ml.decompression import FLAG_ADC_SATURATED, FLAG_LEAD_OFF, FLAG_R_PEAK, FLAG_RLD_OFF
from app.ml.quality import (
    VETO_FRACTION,
    assess_quality,
    hardware_veto,
    is_flatline,
    merge_windows,
    spectral_sqi,
    window_bounds,
)
from app.ml.rpeak_detection import (
    beat_sqi,
    clean_signal,
    compensate_firmware_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)
from tests.ecg_synth import SAMPLE_RATE, synth_ecg

THRESHOLDS = QualityThresholds(
    window_samples=10 * SAMPLE_RATE,
    flatline_mv=0.020,
    psqi_min=0.50,
    ksqi_min=5.0,
    bassqi_min=0.90,
    bsqi_min=0.80,
    bsqi_tolerance_samples=75,
)


def _firmware(flags: np.ndarray) -> np.ndarray:
    """Los picos del firmware **como los ve el motor**.

    `analyze_batch` no compara el tren crudo: le saca los dobletes del
    refractario de 200 ms del MCU y lo corre 250 ms hacia atrás, hasta el pico
    real. Los tests hacen lo mismo para no medir un tren de picos que sobre el
    chaleco no existe.
    """
    return compensate_firmware_peaks(
        firmware_rpeaks(flags),
        lag_samples=int(0.250 * SAMPLE_RATE),
        refractory_samples=int(0.300 * SAMPLE_RATE),
    )


def _assess(signal: np.ndarray, flags: np.ndarray) -> list[SignalQualityLevel]:
    cleaned = clean_signal(signal, SAMPLE_RATE)
    report = assess_quality(
        signal,
        flags,
        cleaned,
        _firmware(flags),
        detect_rpeaks(cleaned, SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
        thresholds=THRESHOLDS,
    )
    return [window.level for window in report.windows]


# --------------------------------------------------------------------------- #
# Ventaneo
# --------------------------------------------------------------------------- #


def test_las_ventanas_cubren_la_senal_entera_sin_huecos() -> None:
    """Un tramo sin nivel de calidad no se distingue de uno bueno en el visor."""
    for n in (1, 999, 5_000, 12_345, 1_800_000):
        bounds = window_bounds(n, 5_000)
        assert bounds[0][0] == 0
        assert sum(length for _, length in bounds) == n
        for (start, length), (next_start, _) in zip(bounds, bounds[1:], strict=False):
            assert start + length == next_start


def test_una_senal_mas_corta_que_la_ventana_es_una_sola_ventana() -> None:
    assert window_bounds(1_200, 5_000) == [(0, 1_200)]


# --------------------------------------------------------------------------- #
# Capa A — bits del hardware
# --------------------------------------------------------------------------- #


def test_un_rebote_de_una_muestra_no_invalida_diez_segundos() -> None:
    flags = np.zeros(5_000, dtype=np.uint8)
    flags[100] = FLAG_LEAD_OFF
    assert hardware_veto(flags) is None


def test_un_electrodo_despegado_invalida_la_ventana() -> None:
    flags = np.zeros(5_000, dtype=np.uint8)
    flags[: int(5_000 * VETO_FRACTION) + 1] = FLAG_LEAD_OFF
    assert hardware_veto(flags) == "lead_off"


def test_la_saturacion_sin_pierna_derecha_no_cuenta_como_artefacto() -> None:
    """`RLD_OFF` degrada el modo común: la señal se va al riel por eso, no por el paciente.

    Es la regla 2 de `INTEGRACION.md` §4.5. Marcarlo como saturación del ADC
    culparía al paciente de un problema de colocación del electrodo de referencia.
    """
    flags = np.full(5_000, FLAG_ADC_SATURATED | FLAG_RLD_OFF, dtype=np.uint8)
    assert hardware_veto(flags) is None

    flags = np.full(5_000, FLAG_ADC_SATURATED, dtype=np.uint8)
    assert hardware_veto(flags) == "saturated"


def test_la_linea_plana_se_mide_con_percentiles_y_no_con_min_max() -> None:
    """Un spike de conmutación no puede hacer pasar por señal a una línea plana."""
    flat = np.zeros(5_000, dtype=np.float32)
    flat[2_500] = 5.0  # un solo pico enorme
    assert is_flatline(flat, 0.020)


# --------------------------------------------------------------------------- #
# Capa B — índices espectrales
# --------------------------------------------------------------------------- #


def test_el_ruido_gaussiano_puro_no_pasa_el_gate() -> None:
    """El falso-pase que `nk.ecg_quality(zhao2018)` comete y este gate no.

    Medido: `zhao2018` devuelve `Excellent` para ruido blanco sin un solo QRS.
    Es el error más caro posible —dar por analizable un tramo que no tiene
    señal—, y la causa es conocida: NeuroKit descartó el índice qSQI, el que
    medía coincidencia entre detectores de R.
    """
    rng = np.random.default_rng(3)
    noise = rng.normal(0.0, 0.3, 10 * SAMPLE_RATE).astype(np.float32)
    psqi, ksqi, bassqi = spectral_sqi(noise, SAMPLE_RATE)

    # La curtosis de una gaussiana es 3: por ahí se lo atrapa sin ambigüedad.
    assert ksqi < THRESHOLDS.ksqi_min
    assert psqi < THRESHOLDS.psqi_min
    assert _assess(noise, np.zeros(noise.size, dtype=np.uint8)) == [SignalQualityLevel.BAD]


def test_un_ecg_limpio_pasa_el_gate() -> None:
    result = synth_ecg(duration_s=60.0)
    levels = _assess(result.signal_mv, result.flags)
    assert set(levels) == {SignalQualityLevel.GOOD}


def test_la_deriva_de_linea_de_base_degrada_el_bassqi() -> None:
    """Y por eso el basSQI se mide sobre la señal CRUDA.

    Sobre la filtrada el pasa-altos ya eliminó la deriva y el índice no
    discrimina nada (medido: 0,879 sin deriva contra 0,880 con deriva fuerte).
    """
    result = synth_ecg(duration_s=10.0)
    drift = 0.6 * np.sin(2 * np.pi * 0.15 * np.arange(result.signal_mv.size) / SAMPLE_RATE)

    _, _, clean_bas = spectral_sqi(result.signal_mv, SAMPLE_RATE)
    _, _, drifted_bas = spectral_sqi((result.signal_mv + drift).astype(np.float32), SAMPLE_RATE)
    assert clean_bas >= THRESHOLDS.bassqi_min > drifted_bas


def test_la_banda_de_baseline_no_castiga_el_ritmo_del_paciente() -> None:
    """Con la banda 0-1 Hz del paper, a 60 lpm el fundamental cardíaco cuenta
    como deriva y un ECG perfectamente limpio se rechaza. Medido: 0,907 contra
    un umbral de 0,90. Con 0-0,5 Hz da 0,987."""
    for bpm in (55.0, 60.0, 75.0, 100.0):
        result = synth_ecg(duration_s=20.0, bpm=bpm)
        _, _, bassqi = spectral_sqi(result.signal_mv, SAMPLE_RATE)
        assert bassqi >= THRESHOLDS.bassqi_min, f"{bpm} lpm degradado por su propio ritmo"


# --------------------------------------------------------------------------- #
# bSQI — el índice que NeuroKit tiró
# --------------------------------------------------------------------------- #


def test_el_bsqi_vale_uno_cuando_los_dos_detectores_coinciden() -> None:
    peaks = np.arange(0, 10_000, 500, dtype=np.int64)
    assert beat_sqi(peaks, peaks, 75) == pytest.approx(1.0)
    assert beat_sqi(peaks, peaks + 40, 75) == pytest.approx(1.0)


def test_el_bsqi_no_premia_a_un_detector_que_dispara_de_mas() -> None:
    """El emparejamiento es 1 a 1: tres disparos sobre el mismo QRS no son tres aciertos."""
    firmware = np.array([1_000], dtype=np.int64)
    detected = np.array([980, 1_000, 1_020], dtype=np.int64)
    assert beat_sqi(firmware, detected, 75) == pytest.approx(2 * 1 / 4)


def test_el_bsqi_es_cero_si_uno_de_los_dos_no_vio_nada() -> None:
    peaks = np.arange(0, 5_000, 500, dtype=np.int64)
    empty = np.empty(0, dtype=np.int64)
    assert beat_sqi(peaks, empty, 75) == 0.0
    assert beat_sqi(empty, empty, 75) == 0.0


def test_el_flag_del_firmware_cuenta_un_latido_por_corrida_y_no_por_muestra() -> None:
    """El flag viaja comprimido en corridas RLE: el mismo latido puede llegar
    marcado en varias muestras seguidas, y contarlas todas hundiría el bSQI sin
    que haya un solo desacuerdo real."""
    flags = np.zeros(1_000, dtype=np.uint8)
    flags[100:105] = FLAG_R_PEAK
    flags[600:603] = FLAG_R_PEAK
    assert firmware_rpeaks(flags).tolist() == [100, 600]


def test_el_pico_del_firmware_se_corre_hasta_el_pico_real() -> None:
    """El bit no marca el pico: marca la confirmación, 250 ms después.

    Medido por el equipo de firmware sobre el chaleco: 160 ms de retardo de
    grupo del FIR de 161 taps, 40 ms de la cascada de detección y hasta 100 ms
    de ventana de confirmación.
    """
    marcados = np.array([500, 1_500, 2_500], dtype=np.int64)
    corridos = compensate_firmware_peaks(marcados, lag_samples=125, refractory_samples=0)
    assert corridos.tolist() == [375, 1_375, 2_375]


def test_los_dobletes_del_refractario_del_mcu_no_cuentan_dos_latidos() -> None:
    """El detector del MCU queda ciego 200 ms y vuelve a confirmar sobre la cola
    del mismo complejo: 31 de 110 intervalos por debajo de 300 ms en las
    capturas del chaleco. Contar los dos hundiría el bSQI sin que haya un solo
    desacuerdo real, que es lo mismo que ya se corrige con las corridas RLE."""
    con_doblete = np.array([500, 610, 1_500], dtype=np.int64)
    limpio = compensate_firmware_peaks(con_doblete, lag_samples=0, refractory_samples=150)
    assert limpio.tolist() == [500, 1_500]


def test_los_latidos_del_arranque_del_lote_sin_contraparte_se_descartan() -> None:
    """Se confirmaron en el lote anterior: correrlos daría un índice negativo."""
    peaks = np.array([50, 900], dtype=np.int64)
    assert compensate_firmware_peaks(peaks, lag_samples=125, refractory_samples=0).tolist() == [775]


def test_sin_r_peaks_del_firmware_el_bsqi_no_se_aplica() -> None:
    """No se puede medir el acuerdo con un detector que no habló.

    Un registro importado (MIT-BIH) o de un firmware viejo no trae el bit. Si el
    bSQI se aplicara igual daría 0 en todas las ventanas y degradaría el estudio
    entero por una ausencia que no dice nada sobre la señal.
    """
    result = synth_ecg(duration_s=30.0)
    sin_flags = np.zeros(result.signal_mv.size, dtype=np.uint8)
    cleaned = clean_signal(result.signal_mv, SAMPLE_RATE)
    report = assess_quality(
        result.signal_mv,
        sin_flags,
        cleaned,
        _firmware(sin_flags),
        detect_rpeaks(cleaned, SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
        thresholds=THRESHOLDS,
    )
    assert report.firmware_peaks_available is False
    assert all(window.bsqi is None for window in report.windows)
    assert {window.level for window in report.windows} == {SignalQualityLevel.GOOD}


# --------------------------------------------------------------------------- #
# Casos degenerados
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "signal",
    [
        np.zeros(5_000, dtype=np.float32),
        np.full(5_000, 2.5, dtype=np.float32),
        np.full(5_000, np.nan, dtype=np.float32),
    ],
    ids=["ceros", "continua", "nan"],
)
def test_una_ventana_degenerada_no_hace_explotar_el_gate(signal: np.ndarray) -> None:
    """Devuelve `bad`, no una excepción. Un lote no puede fallar entero porque
    diez segundos de señal sean basura."""
    levels = _assess(signal, np.zeros(signal.size, dtype=np.uint8))
    assert levels == [SignalQualityLevel.BAD]


def test_los_tramos_contiguos_del_mismo_nivel_se_fusionan() -> None:
    """Una hora limpia es una fila, no 360."""
    result = synth_ecg(duration_s=120.0)
    cleaned = clean_signal(result.signal_mv, SAMPLE_RATE)
    report = assess_quality(
        result.signal_mv,
        result.flags,
        cleaned,
        _firmware(result.flags),
        detect_rpeaks(cleaned, SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
        thresholds=THRESHOLDS,
    )
    merged = merge_windows(report.windows)
    assert len(report.windows) == 12
    assert len(merged) == 1
    assert merged[0].start_sample == 0
    assert merged[0].length_samples == result.signal_mv.size


def test_el_electrodo_despegado_parte_el_intervalo_en_tres() -> None:
    result = synth_ecg(duration_s=120.0)
    signal = result.signal_mv.copy()
    flags = result.flags.copy()
    flags[60 * SAMPLE_RATE : 70 * SAMPLE_RATE] |= FLAG_LEAD_OFF

    cleaned = clean_signal(signal, SAMPLE_RATE)
    report = assess_quality(
        signal,
        flags,
        cleaned,
        _firmware(flags),
        detect_rpeaks(cleaned, SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
        thresholds=THRESHOLDS,
    )
    merged = merge_windows(report.windows)
    assert [item.level for item in merged] == [
        SignalQualityLevel.GOOD,
        SignalQualityLevel.BAD,
        SignalQualityLevel.GOOD,
    ]
    assert merged[1].reason == "lead_off"
    # Y las muestras de ese tramo quedan fuera de lo analizable.
    assert not report.analyzable[62 * SAMPLE_RATE]
    assert report.analyzable[30 * SAMPLE_RATE]
