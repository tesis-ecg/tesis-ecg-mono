"""Etapa 1 — el gate de calidad.

Lo que estos tests fijan no son umbrales sino **modos de falla**. Cada uno
corresponde a una forma concreta en que un Holter con electrodos secos se
degrada, y a un falso-pase medido de las alternativas de biblioteca.
"""

from dataclasses import replace

import numpy as np
import pytest

from app.db.models.signal_quality import SignalQualityLevel
from app.ml.contracts import QualityThresholds
from app.ml.decompression import FLAG_ADC_SATURATED, FLAG_LEAD_OFF, FLAG_R_PEAK, FLAG_RLD_OFF
from app.ml.quality import (
    QRS_BAND,
    VETO_FRACTION,
    assess_quality,
    deinterfere,
    hardware_veto,
    is_flatline,
    mains_settle_samples,
    merge_windows,
    remove_mains,
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


def _windows(
    signal: np.ndarray, flags: np.ndarray, thresholds: QualityThresholds = THRESHOLDS
) -> list[tuple[SignalQualityLevel, str]]:
    cleaned = clean_signal(signal, SAMPLE_RATE)
    report = assess_quality(
        signal,
        flags,
        cleaned,
        _firmware(flags),
        detect_rpeaks(cleaned, SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
        thresholds=thresholds,
    )
    return [(window.level, window.reason) for window in report.windows]


def _assess(signal: np.ndarray, flags: np.ndarray) -> list[SignalQualityLevel]:
    return [level for level, _ in _windows(signal, flags)]


def _time(n_samples: int) -> np.ndarray:
    return np.arange(n_samples) / SAMPLE_RATE


def _with_mains(
    signal: np.ndarray, mains_hz: float = 50.0, amplitude_mv: float = 2.0
) -> np.ndarray:
    """La red como la capta un electrodo seco: fundamental de 2 mV y una armónica."""
    t = _time(signal.size)
    mains = amplitude_mv * np.sin(2 * np.pi * mains_hz * t)
    mains += 0.15 * amplitude_mv * np.sin(2 * np.pi * 2 * mains_hz * t)
    return (signal + mains).astype(np.float32)


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
# Remoción de la red antes de los índices
# --------------------------------------------------------------------------- #

MAINS_ON = replace(THRESHOLDS, mains_hz=50.0)


def test_quitar_la_red_no_rescata_al_ruido_gaussiano() -> None:
    """El notch saca líneas angostas, no recorta a la banda del QRS como
    `ecg_clean`: el ruido blanco sigue siendo gaussiano y sigue sin pasar."""
    rng = np.random.default_rng(3)
    noise = rng.normal(0.0, 0.3, 10 * SAMPLE_RATE).astype(np.float32)

    psqi, ksqi, _ = spectral_sqi(remove_mains(noise, SAMPLE_RATE, 50.0), SAMPLE_RATE)
    assert psqi < 0.5
    assert ksqi < 5.0
    assert _windows(noise, np.zeros(noise.size, dtype=np.uint8), MAINS_ON)[0][0] is (
        SignalQualityLevel.BAD
    )


@pytest.mark.parametrize("mains_hz", [50.0, 50.2], ids=["nominal", "reloj_del_chaleco"])
def test_quitar_la_red_recupera_un_ecg_bajo_2_mv_de_interferencia(mains_hz: float) -> None:
    """Medido en el chaleco: con ~2 mV de red un ECG perfectamente visible da
    kSQI ≈ 2 y se rechaza entero. 50,2 Hz es donde la red aparece de verdad en
    las capturas (el reloj de muestreo va ~0,4 % lento): el notch tiene que
    alcanzar ahí también, no solo en el 50,00 exacto."""
    result = synth_ecg(duration_s=30.0)
    contaminated = _with_mains(result.signal_mv, mains_hz=mains_hz)

    _, raw_ksqi, _ = spectral_sqi(contaminated[: 10 * SAMPLE_RATE], SAMPLE_RATE)
    _, ksqi, _ = spectral_sqi(
        remove_mains(contaminated, SAMPLE_RATE, 50.0)[: 10 * SAMPLE_RATE], SAMPLE_RATE
    )
    assert raw_ksqi < THRESHOLDS.ksqi_min <= ksqi

    assert _windows(contaminated, result.flags, MAINS_ON) == [(SignalQualityLevel.GOOD, "ok")] * 3
    # Apagada, el mismo tramo se rechaza por la curtosis: es la falla que corrige.
    assert (
        _windows(contaminated, result.flags, THRESHOLDS) == [(SignalQualityLevel.BAD, "ksqi")] * 3
    )


def test_con_la_red_apagada_solo_se_quita_la_media() -> None:
    """Y los índices quedan como sobre la señal cruda: Welch ya resta la media
    de cada segmento y la curtosis no ve un corrimiento constante."""
    result = synth_ecg(duration_s=10.0)
    signal = _with_mains(result.signal_mv + 48.0)

    output = remove_mains(signal, SAMPLE_RATE, 0.0)
    assert output.dtype == np.float32
    assert output is not signal
    np.testing.assert_allclose(output, signal - signal.astype(np.float64).mean(), atol=1e-4)
    np.testing.assert_allclose(
        spectral_sqi(output, SAMPLE_RATE), spectral_sqi(signal, SAMPLE_RATE), rtol=1e-3
    )


def test_el_notch_saca_las_armonicas_y_respeta_la_banda_del_ecg() -> None:
    t = _time(20 * SAMPLE_RATE)
    for frequency in (50.0, 100.0, 150.0, 200.0):
        tone = np.sin(2 * np.pi * frequency * t).astype(np.float32)
        residual = remove_mains(tone, SAMPLE_RATE, 50.0)[SAMPLE_RATE:-SAMPLE_RATE]
        assert np.sqrt(np.mean(residual**2)) < 0.01, f"{frequency} Hz"
    # 30 Hz está adentro de la banda de los índices y del QRS: no se toca.
    tone = np.sin(2 * np.pi * 30.0 * t).astype(np.float32)
    kept = remove_mains(tone, SAMPLE_RATE, 50.0)
    np.testing.assert_allclose(np.std(kept), np.std(tone), rtol=0.01)


def test_las_armonicas_se_cortan_antes_de_nyquist() -> None:
    """62,5 Hz a 500 Hz pone la cuarta armónica en Nyquist exacto, donde
    `iirnotch` rechaza el diseño. Tiene que quedar afuera, no explotar."""
    signal = synth_ecg(duration_s=10.0).signal_mv
    output = remove_mains(signal, SAMPLE_RATE, 62.5)
    assert output.shape == signal.shape
    assert np.isfinite(output).all()


@pytest.mark.parametrize("n_samples", [0, 1, 5, 9, 10, 100])
def test_una_senal_mas_corta_que_el_padlen_no_explota(n_samples: int) -> None:
    signal = np.linspace(40.0, 41.0, n_samples, dtype=np.float32)
    output = remove_mains(signal, SAMPLE_RATE, 50.0)
    assert output.dtype == np.float32
    assert output.shape == (n_samples,)
    assert np.isfinite(output).all()
    if n_samples:
        assert abs(float(output.mean())) < 1e-3


def test_los_nan_van_a_cero_despues_de_quitar_la_media() -> None:
    """La convención de `clean_signal` (NaN → 0), pero sobre la señal ya
    centrada: un hueco no deja un escalón de 48 mV contra un cero absoluto."""
    signal = (synth_ecg(duration_s=10.0).signal_mv + 48.0).astype(np.float32)
    signal[1_000:1_200] = np.nan
    signal[3_000] = np.inf

    output = remove_mains(signal, SAMPLE_RATE, 50.0)
    assert np.isfinite(output).all()
    assert np.abs(output).max() < 5.0
    assert remove_mains(np.full(5_000, np.nan, dtype=np.float32), SAMPLE_RATE, 50.0).tolist() == (
        [0.0] * 5_000
    )


def test_un_electrodo_que_solo_capta_red_sigue_rechazado() -> None:
    """La línea plana mira la señal CRUDA: un electrodo flotando que capta 1 mV
    de 50 Hz no es plano. Y quitarle la red no lo convierte en ECG: lo que queda
    es el ruido del amplificador, gaussiano, y los índices lo rechazan."""
    t = _time(10 * SAMPLE_RATE)
    noise = np.random.default_rng(5).normal(0.0, 0.02, t.size)
    floating = (1.0 * np.sin(2 * np.pi * 50.0 * t) + noise).astype(np.float32)
    assert not is_flatline(floating, THRESHOLDS.flatline_mv)
    assert _windows(floating, np.zeros(floating.size, dtype=np.uint8), MAINS_ON) == [
        (SignalQualityLevel.BAD, "psqi")
    ]


@pytest.mark.parametrize("mains_hz", [1e-6, 0.5, 25.0, 44.9])
def test_una_red_que_no_existe_es_un_error_y_no_un_bucle(mains_hz: float) -> None:
    """Con 0,5 Hz serían 499 notch que se comen el ECG; con 1e-6 el bucle de
    armónicas no termina. El setting ya lo impide, el motor tampoco lo acepta."""
    with pytest.raises(ValueError, match="no es una red"):
        remove_mains(np.zeros(5_000, dtype=np.float32), SAMPLE_RATE, mains_hz)


def _band_noise(n_samples: int, seed: int = 1) -> np.ndarray:
    """Ruido gaussiano recortado a la banda del QRS: pasa el pSQI y no tiene un
    solo latido. Lo único que lo delata es la curtosis, ~3."""
    from scipy import signal as sp_signal

    numerator, denominator = sp_signal.butter(4, QRS_BAND, btype="band", fs=SAMPLE_RATE)
    noise = sp_signal.filtfilt(
        numerator, denominator, np.random.default_rng(seed).normal(0.0, 1.0, n_samples)
    )
    return (0.3 * noise / noise.std()).astype(np.float32)


@pytest.mark.parametrize("rail_flag", [FLAG_LEAD_OFF, FLAG_ADC_SATURATED], ids=["lead_off", "riel"])
@pytest.mark.parametrize("noise_first", [False, True], ids=["despues", "antes"])
def test_el_riel_no_hace_sonar_al_notch_sobre_la_ventana_vecina(
    rail_flag: int, noise_first: bool
) -> None:
    """Un escalón de 400 mV filtrado deja milivoltios oscilando a 50 Hz a los
    dos lados —`filtfilt` es de fase cero— durante casi un segundo. Con el notch
    corriendo por encima del riel, ruido sin un QRS al lado de un electrodo
    despegado pasaba de kSQI 3 a 190 y el gate lo daba por bueno, antes y
    después del riel. El notch se corta en el riel."""
    ecg = synth_ecg(duration_s=40.0)
    signal = ecg.signal_mv.copy()
    flags = ecg.flags.copy()
    window = 10 * SAMPLE_RATE
    noise_index, rail_index = (1, 2) if noise_first else (2, 1)
    rail = slice(rail_index * window, (rail_index + 1) * window)
    noise = slice(noise_index * window, (noise_index + 1) * window)
    signal[rail] = 403.0
    flags[rail] = rail_flag
    signal[noise] = _band_noise(window)
    flags[noise] = 0

    windows = _windows(signal, flags, MAINS_ON)
    assert windows[rail_index] == (
        SignalQualityLevel.BAD,
        "lead_off" if rail_flag == FLAG_LEAD_OFF else "saturated",
    )
    assert windows[noise_index] == (SignalQualityLevel.BAD, "ksqi")
    # Y el ECG del otro lado del riel no pierde nada por el corte.
    assert windows[3] == (SignalQualityLevel.GOOD, "ok")


def test_el_arranque_del_lote_no_regala_la_primera_ventana() -> None:
    """El transitorio de arranque de `filtfilt` sobre 20 mV pico a pico de red
    en 50,25 Hz (el corrimiento medido en el chaleco) infla la curtosis del
    primer segundo: sin descartarlo, ruido sin latidos salía bueno en la
    primera ventana y malo en todas las demás."""
    noise = _with_mains(_band_noise(60 * SAMPLE_RATE), mains_hz=50.25, amplitude_mv=10.0)
    windows = _windows(noise, np.zeros(noise.size, dtype=np.uint8), MAINS_ON)
    assert windows == [(SignalQualityLevel.BAD, "ksqi")] * 6


def test_la_red_fuerte_fuera_de_frecuencia_se_saca_igual_en_todas_las_ventanas() -> None:
    """10 mV pico a pico en 50,25 Hz es el chaleco enchufado. La primera ventana
    tiene que dar lo mismo que las del medio, y las del medio tienen que pasar:
    con Q = 30 el residuo de red ya volvía a tumbar la curtosis."""
    result = synth_ecg(duration_s=60.0)
    contaminated = _with_mains(result.signal_mv, mains_hz=50.25, amplitude_mv=5.0)
    assert _windows(contaminated, result.flags, MAINS_ON) == [(SignalQualityLevel.GOOD, "ok")] * 6


def test_el_notch_no_cruza_el_riel_y_conserva_el_escalon() -> None:
    """Fuera del riel la señal sale sin red y con su nivel; en el riel queda la
    cruda (el notch corre sobre un puente, no sobre el escalón). Así pSQI y
    basSQI ven el mismo escalón que sin quitar la red, y la curtosis solo mira lo
    asentado: lejos del riel y del arranque."""
    t = _time(30 * SAMPLE_RATE)
    signal = (1.0 + 2.0 * np.sin(2 * np.pi * 50.0 * t)).astype(np.float32)
    flags = np.zeros(signal.size, dtype=np.uint8)
    rail = slice(10 * SAMPLE_RATE, 12 * SAMPLE_RATE)
    signal[rail] = 403.0
    flags[rail] = FLAG_LEAD_OFF

    output, settled = deinterfere(signal, flags, SAMPLE_RATE, 50.0)
    margin = mains_settle_samples(SAMPLE_RATE, 50.0)
    assert SAMPLE_RATE * 0.9 < margin < SAMPLE_RATE * 1.0
    mean = float(signal.astype(np.float64).mean())
    np.testing.assert_allclose(output[rail], 403.0 - mean, rtol=1e-5)
    # Lejos de los bordes: la red se fue y el nivel del tramo quedó.
    interior = output[3 * SAMPLE_RATE : 7 * SAMPLE_RATE]
    np.testing.assert_allclose(interior, 1.0 - mean, atol=0.01)
    assert not settled[rail].any()
    assert not settled[:margin].any() and settled[margin]
    assert not settled[rail.start - margin : rail.start].any()
    assert settled[rail.start - margin - 1]
    assert not settled[rail.stop : rail.stop + margin].any()
    assert settled[rail.stop + margin]
    assert not settled[-margin:].any()

    # Apagada: sin notch, sin margen, y la curtosis igual deja afuera el riel.
    output, settled = deinterfere(signal, flags, SAMPLE_RATE, 0.0)
    np.testing.assert_allclose(output, signal - mean, atol=1e-4)
    np.testing.assert_array_equal(settled, flags == 0)


@pytest.mark.parametrize("mains_hz", [0.0, 50.0], ids=["red_apagada", "red_activa"])
def test_unas_muestras_en_el_riel_no_inflan_la_curtosis(mains_hz: float) -> None:
    """Menos del 5 % no veta la ventana, pero 0,4 s de un riel de 400 mV dominan
    el cuarto momento: el ruido de alrededor pasaba la curtosis de arrastre.
    Las muestras inválidas no entran en el kSQI, con la red activa o no."""
    result = synth_ecg(duration_s=30.0)
    signal = result.signal_mv.copy()
    flags = result.flags.copy()
    window = slice(10 * SAMPLE_RATE, 20 * SAMPLE_RATE)
    signal[window] = _band_noise(10 * SAMPLE_RATE)
    flags[window] = 0
    rail = slice(18 * SAMPLE_RATE, 18 * SAMPLE_RATE + int(10 * SAMPLE_RATE * 0.04))
    signal[rail] = 403.0
    flags[rail] = FLAG_LEAD_OFF

    windows = _windows(signal, flags, replace(THRESHOLDS, mains_hz=mains_hz))
    assert windows[1] == (SignalQualityLevel.BAD, "ksqi")


@pytest.mark.parametrize("mains_hz", [0.0, 50.0], ids=["red_apagada", "red_activa"])
def test_un_hueco_de_nan_es_una_linea_plana(mains_hz: float) -> None:
    """Quitar la red pone los NaN en 0, y tres segundos planos en cero inflan la
    curtosis: la ventana salía buena con kSQI 22. Un hueco por encima de la
    fracción de veto no es señal. Uno chico no tumba los otros 9,8 s."""
    thresholds = replace(THRESHOLDS, mains_hz=mains_hz)
    result = synth_ecg(duration_s=30.0)
    signal = result.signal_mv.copy()
    signal[12 * SAMPLE_RATE : 15 * SAMPLE_RATE] = np.nan
    assert _windows(signal, result.flags, thresholds) == [
        (SignalQualityLevel.GOOD, "ok"),
        (SignalQualityLevel.BAD, "flatline"),
        (SignalQualityLevel.GOOD, "ok"),
    ]

    signal = result.signal_mv.copy()
    signal[12 * SAMPLE_RATE : 12 * SAMPLE_RATE + 100] = np.nan
    assert _windows(signal, result.flags, thresholds) == [(SignalQualityLevel.GOOD, "ok")] * 3


# --------------------------------------------------------------------------- #
# Motivo: el primer índice que falla
# --------------------------------------------------------------------------- #


def _ten_seconds_of_ecg() -> np.ndarray:
    return synth_ecg(duration_s=10.0).signal_mv.astype(np.float64)


def _reason(signal: np.ndarray) -> str:
    """Motivo de una ventana sola, sin red removida para que nada la corrija."""
    signal = signal.astype(np.float32)
    [(level, reason)] = _windows(signal, np.zeros(signal.size, dtype=np.uint8))
    assert level is SignalQualityLevel.BAD
    return reason


def test_energia_fuera_de_la_banda_del_qrs_falla_solo_el_psqi() -> None:
    ecg = _ten_seconds_of_ecg()
    signal = ecg + 0.1 * np.sin(2 * np.pi * 25.0 * _time(ecg.size))
    psqi, ksqi, bassqi = spectral_sqi(signal.astype(np.float32), SAMPLE_RATE)
    assert psqi < THRESHOLDS.psqi_min
    assert ksqi >= THRESHOLDS.ksqi_min and bassqi >= THRESHOLDS.bassqi_min
    assert _reason(signal) == "psqi"


def test_la_red_sin_remover_falla_solo_el_ksqi() -> None:
    """50 Hz cae fuera de las bandas de pSQI y basSQI (≤ 40 Hz): solo lo ve la
    curtosis. Es el caso real del chaleco."""
    signal = _with_mains(_ten_seconds_of_ecg().astype(np.float32))
    psqi, ksqi, bassqi = spectral_sqi(signal, SAMPLE_RATE)
    assert ksqi < THRESHOLDS.ksqi_min
    assert psqi >= THRESHOLDS.psqi_min and bassqi >= THRESHOLDS.bassqi_min
    assert _reason(signal) == "ksqi"


def test_la_deriva_falla_solo_el_bassqi() -> None:
    ecg = _ten_seconds_of_ecg()
    signal = ecg + 0.2 * np.sin(2 * np.pi * 0.3 * _time(ecg.size))
    psqi, ksqi, bassqi = spectral_sqi(signal.astype(np.float32), SAMPLE_RATE)
    assert bassqi < THRESHOLDS.bassqi_min
    assert psqi >= THRESHOLDS.psqi_min and ksqi >= THRESHOLDS.ksqi_min
    assert _reason(signal) == "bassqi"


def test_si_fallan_psqi_y_ksqi_manda_el_psqi() -> None:
    """Orden fijo: el motivo no puede depender de cuál índice se miró primero."""
    noise = np.random.default_rng(3).normal(0.0, 0.3, 10 * SAMPLE_RATE)
    psqi, ksqi, _ = spectral_sqi(noise.astype(np.float32), SAMPLE_RATE)
    assert psqi < THRESHOLDS.psqi_min and ksqi < THRESHOLDS.ksqi_min
    assert _reason(noise) == "psqi"


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
