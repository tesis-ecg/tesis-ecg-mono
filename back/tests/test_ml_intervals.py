"""Mediciones de intervalos (QT, QTc, amplitud R) — `app/ml/intervals.py`.

Lo que se verifica es lo que el benchmark de la QT Database dejó como contrato:
el QT de `prominence` con la definición del plan, los controles por latido que
sacan de la mediana lo que el delineador no puede medir (un latido perdido por
el detector, una T que no entra en el segmento, un R fuera del QRS, un QRS
negativo, la ectopia), que ningún error de NeuroKit tumbe el bloque, que la
amplitud R salga de la señal sin pasabajos y que el ancho de QRS no se reporte.
También se fija lo que el módulo **no** puede medir: un QT largo sale corto con
cobertura completa, y eso tiene que seguir visible en CI.

Dos generadores: `tests/ecg_synth.synth_ecg` (el de todo el motor) y uno local
con la T escalada por Fridericia. El de `ecg_synth` pone la T a +200 ms a
cualquier frecuencia: su QT no cambia con la FC, así que su QTc no puede ser
invariante y para eso no sirve.
"""

from __future__ import annotations

import importlib

import numpy as np
import pytest

from app.ml import intervals
from app.ml.intervals import (
    IntervalMeasurement,
    IntervalThresholds,
    fridericia_ms,
    measure_beats,
    measure_intervals,
)
from app.ml.rpeak_detection import clean_signal, detect_rpeaks
from tests.ecg_synth import SAMPLE_RATE, synth_ecg

DEFAULTS = IntervalThresholds()
#: T de QT normal (QTc ~395 ms) y de QT largo (QTc ~570 ms) del generador local.
NORMAL_T = (0.25, 0.040)
LONG_T = (0.38, 0.060)


def _beat(
    t: np.ndarray,
    rr_s: float,
    *,
    t_wave: tuple[float, float] = NORMAL_T,
    ectopic: bool = False,
) -> np.ndarray:
    """El latido de cinco gaussianas de `ecg_synth`, con la T escalada por RR^(1/3).

    QRS y P quedan fijos; el pico y el ancho de la T se escalan con la raíz
    cúbica del RR previo, que es exactamente lo que supone Fridericia. Con la T
    por defecto va a +250 ms a 60 lpm (QT medido ~390 ms), más fisiológica que
    los +200 ms de `ecg_synth`. `ectopic` arma un latido ventricular: QRS tres
    veces más ancho y más alto, T invertida.
    """
    k = float(np.cbrt(rr_s))
    width, height, t_sign = (3.0, 1.3, -1.0) if ectopic else (1.0, 1.0, 1.0)
    t_center, t_sigma = t_wave

    def gauss(center: float, sigma: float, amplitude: float) -> np.ndarray:
        return amplitude * np.exp(-0.5 * ((t - center) / sigma) ** 2)

    return (
        gauss(-0.16, 0.025, 0.12)
        + gauss(-0.02, 0.010 * width, -0.15 * height)
        + gauss(0.0, 0.012 * width, 1.0 * height)
        + gauss(0.025 * width, 0.012 * width, -0.25 * height)
        + gauss(t_center * k, t_sigma * k, 0.35 * t_sign)
    )


def _ecg_from_rr(
    rr_seconds: np.ndarray,
    *,
    noise_uv: float = 8.0,
    seed: int = 3,
    t_wave: tuple[float, float] = NORMAL_T,
    ectopic: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """ECG en mV con un latido por intervalo; devuelve (señal, R verdaderos).

    `ectopic`, si viene, marca por latido (uno más que intervalos) cuáles son
    ventriculares.
    """
    rr_seconds = np.asarray(rr_seconds, dtype=np.float64)
    times = 0.5 + np.concatenate(([0.0], np.cumsum(rr_seconds)))
    n = int((times[-1] + 1.0) * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    signal = np.zeros(n)
    for i, center in enumerate(times):
        rr_prev = rr_seconds[i - 1] if i > 0 else rr_seconds[0]
        low = max(0, int((center - 0.5) * SAMPLE_RATE))
        high = min(n, int((center + 0.8) * SAMPLE_RATE))
        is_ectopic = bool(ectopic[i]) if ectopic is not None else False
        signal[low:high] += _beat(t[low:high] - center, rr_prev, t_wave=t_wave, ectopic=is_ectopic)
    signal += np.random.default_rng(seed).normal(0.0, noise_uv / 1000.0, n)
    rpeaks = np.round(times * SAMPLE_RATE).astype(np.int64)
    return signal.astype(np.float32), rpeaks


def _regular(bpm: float, seconds: float = 300.0, **kwargs: object) -> tuple[np.ndarray, np.ndarray]:
    rr = 60.0 / bpm
    return _ecg_from_rr(np.full(int(seconds / rr), rr), **kwargs)  # type: ignore[arg-type]


def _ectopy(
    every: int, beats: int = 350, bpm: float = 70.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ritmo sinusal con un ventricular cada `every` latidos (2 = bigeminismo).

    Acople de 0,6 ciclos y pausa compensadora de 1,4: la suma de los dos es la de
    dos ciclos, como en un extrasístole ventricular. Devuelve (señal, R, ectópicos).
    """
    period = 60.0 / bpm
    rr, ectopic = [], [False]
    for i in range(beats):
        next_is_ectopic = (i + 1) % every == every - 1
        this_is_ectopic = i % every == every - 1
        rr.append(period * (0.6 if next_is_ectopic else 1.4 if this_is_ectopic else 1.0))
        ectopic.append(next_is_ectopic)
    flags = np.array(ectopic)
    signal, rpeaks = _ecg_from_rr(np.array(rr), noise_uv=15.0, ectopic=flags)
    return signal, rpeaks, flags


def _measure(
    raw: np.ndarray,
    rpeaks: np.ndarray,
    *,
    good: np.ndarray | None = None,
    thresholds: IntervalThresholds = DEFAULTS,
    amplitude_from: np.ndarray | None = None,
    dominant: np.ndarray | None = None,
) -> IntervalMeasurement | None:
    cleaned = clean_signal(raw, SAMPLE_RATE)
    mask = np.ones(cleaned.size, dtype=bool) if good is None else good
    source = raw if amplitude_from is None else amplitude_from
    return measure_intervals(
        cleaned, source, rpeaks, mask, SAMPLE_RATE, thresholds, dominant=dominant
    )


def _nominal_qtc_ms(t_wave: tuple[float, float]) -> float:
    """QTc "verdadero" del generador: R_onset ~45 ms antes del R y el fin de la T
    a 2,45 σ de su pico (donde la gaussiana cae al 5 %). Fridericia lo deja fijo."""
    t_center, t_sigma = t_wave
    return (t_center + 2.45 * t_sigma + 0.045) * 1000.0


# --------------------------------------------------------------------------- #
# Rango fisiológico y QTc
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bpm", [60.0, 90.0])
def test_qt_y_qtc_en_rango_fisiologico_a_60_y_90_lpm(bpm: float) -> None:
    synth = synth_ecg(120.0, bpm=bpm, firmware_lag_ms=0.0)
    cleaned = clean_signal(synth.signal_mv, SAMPLE_RATE)
    detected = detect_rpeaks(cleaned, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)

    result = measure_intervals(cleaned, synth.signal_mv, detected, good, SAMPLE_RATE, DEFAULTS)

    assert result is not None
    assert 300.0 <= result.qt_ms <= 450.0
    assert 300.0 <= result.qtc_ms <= 480.0
    assert result.heart_rate_bpm == pytest.approx(bpm, abs=1.0)
    assert result.candidate_heart_rate_bpm == pytest.approx(bpm, abs=1.0)
    assert result.beats >= DEFAULTS.min_beats
    assert result.coverage_ratio >= 0.9
    assert result.beats <= result.candidate_beats


def test_qtc_casi_invariante_entre_60_y_90_lpm() -> None:
    slow = _measure(*_ecg_from_rr(np.full(100, 1.0)))
    fast = _measure(*_ecg_from_rr(np.full(150, 60.0 / 90.0)))

    assert slow is not None and fast is not None
    # El QT sí acompaña a la frecuencia...
    assert slow.qt_ms - fast.qt_ms >= 25.0
    # ...y Fridericia lo corrige por debajo del gate de 30 ms. No del todo: dos
    # tramos del QT medido no escalan con el RR (R_onset sale de una ventana
    # fija alrededor del R y el T_offset, de una ventana fija de 100 ms después
    # del pico de la T), así que Fridericia sobrecorrige un poco a 90 lpm.
    assert abs(slow.qtc_ms - fast.qtc_ms) <= 25.0
    assert abs(slow.qtc_ms - fast.qtc_ms) < slow.qt_ms - fast.qt_ms


def test_fridericia_caso_armado_a_mano() -> None:
    qt = np.array([400.0, 350.0, 360.0, 400.0])
    rr = np.array([1.0, 0.512, 0.729, 0.8])

    qtc = fridericia_ms(qt, rr)

    # 0,512 = 0,8³ y 0,729 = 0,9³: raíces cúbicas exactas.
    assert qtc[0] == pytest.approx(400.0)
    assert qtc[1] == pytest.approx(437.5)
    assert qtc[2] == pytest.approx(400.0)
    assert qtc[3] == pytest.approx(400.0 / 0.8 ** (1.0 / 3.0))


def test_qtc_del_bloque_es_qt_sobre_raiz_cubica_del_rr() -> None:
    # RR constante de 0,8 s (400 muestras exactas): todos los latidos se
    # corrigen por el mismo factor y la mediana del QTc es la del QT / 0,8^(1/3).
    result = _measure(*_ecg_from_rr(np.full(120, 0.8)))

    assert result is not None
    assert result.qtc_ms == pytest.approx(result.qt_ms / 0.8 ** (1.0 / 3.0))
    assert result.heart_rate_bpm == pytest.approx(75.0)


# --------------------------------------------------------------------------- #
# La T que el delineador no ve: techo de FC, T cortada y el límite conocido
# --------------------------------------------------------------------------- #


def test_por_encima_de_100_lpm_no_se_mide() -> None:
    # A 120 lpm `prominence` mira la T solo hasta R + 250 ms: una T normal no
    # entra y el QTc sale corto, en zona de `qtc_short`. El techo de FC lo evita.
    slow = _measure(*_regular(60.0))
    fast_signal, fast_rpeaks = _regular(120.0)
    without_guards = IntervalThresholds(max_heart_rate_bpm=200.0, reject_truncated_t=False)

    assert slow is not None
    assert _measure(fast_signal, fast_rpeaks) is None
    unguarded = _measure(fast_signal, fast_rpeaks, thresholds=without_guards)
    assert unguarded is not None
    assert unguarded.qtc_ms < slow.qtc_ms - 20.0


def test_t_que_no_entra_en_el_segmento_no_se_mide() -> None:
    # QT largo a 80 lpm, por debajo del techo de FC: la T termina después de
    # R + RR/2. Sin el control, el T_offset queda en el borde del segmento y el
    # QTc sale normal con cobertura completa: el falso normal que se evita.
    signal, rpeaks = _regular(80.0, noise_uv=15.0, t_wave=(0.33, 0.050))
    nominal = _nominal_qtc_ms((0.33, 0.050))

    assert DEFAULTS.reject_truncated_t is True
    assert _measure(signal, rpeaks) is None
    unguarded = _measure(signal, rpeaks, thresholds=IntervalThresholds(reject_truncated_t=False))
    assert unguarded is not None
    assert unguarded.coverage_ratio >= 0.9
    assert unguarded.qtc_ms <= nominal - 30.0
    # A 60 lpm la T normal entra entera y el control no cambia nada.
    slow = _regular(60.0)
    assert _measure(*slow) == _measure(
        *slow, thresholds=IntervalThresholds(reject_truncated_t=False)
    )


def test_limite_conocido_el_qt_largo_sale_corto_con_cobertura_completa() -> None:
    # Lo que este módulo NO puede medir, fijado para que siga visible. El
    # T_offset de `prominence` queda a ≤ 100 ms del pico de la T
    # (`peak_prominences(wlen = 200 ms)`): una T ancha termina más lejos y el QT
    # sale corto. A 60 lpm la T entra en el segmento, ningún control la saca y
    # el bloque reporta un QTc ~50 ms corto con todos los latidos válidos. Por
    # esto el QTc es un dato de investigación y no un número por paciente.
    nominal = _nominal_qtc_ms(LONG_T)
    at_60 = _measure(*_regular(60.0, noise_uv=15.0, t_wave=LONG_T))

    assert at_60 is not None
    assert at_60.coverage_ratio >= 0.9
    assert at_60.qtc_ms <= nominal - 30.0
    # A 90 lpm el pico de la T cae fuera del segmento: `prominence` elige otro
    # extremo y da QT de ~240 ms. La cobertura baja y el bloque sale None en vez
    # de un QTc en zona de `qtc_short`.
    assert _measure(*_regular(90.0, noise_uv=15.0, t_wave=LONG_T)) is None


# --------------------------------------------------------------------------- #
# Qué latidos entran
# --------------------------------------------------------------------------- #


def test_menos_de_min_beats_devuelve_none() -> None:
    synth = synth_ecg(20.0, bpm=60.0, firmware_lag_ms=0.0)  # ~19 latidos

    assert _measure(synth.signal_mv, synth.rpeaks) is None
    relaxed = _measure(synth.signal_mv, synth.rpeaks, thresholds=IntervalThresholds(min_beats=10))
    assert relaxed is not None
    assert relaxed.beats >= 10


def test_latidos_fuera_de_good_mask_no_entran() -> None:
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)
    reference = _measure(synth.signal_mv, synth.rpeaks)

    half = synth.signal_mv.size // 2
    # La segunda mitad pasa a ruido muscular grosero: si algún latido de ahí
    # entrara, movería el QT.
    noisy = synth.signal_mv.copy()
    rng = np.random.default_rng(11)
    noisy[half:] += rng.normal(0.0, 0.4, noisy.size - half).astype(np.float32)
    good = np.zeros(noisy.size, dtype=bool)
    good[:half] = True

    cleaned = clean_signal(noisy, SAMPLE_RATE)
    beats = measure_beats(cleaned, noisy, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS)
    # La cobertura es sobre los candidatos, que ya son solo los de la mitad GOOD.
    result = measure_intervals(cleaned, noisy, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS)

    assert beats is not None and result is not None and reference is not None
    assert np.all(beats.rpeaks[beats.valid] < half)
    assert np.all(beats.rpeaks[beats.candidate] < half)
    assert result.beats <= int((synth.rpeaks < half).sum())
    assert result.qt_ms == pytest.approx(reference.qt_ms, abs=4.0)

    assert _measure(noisy, synth.rpeaks, good=np.zeros(noisy.size, dtype=bool)) is None


def test_latido_perdido_no_mete_un_qt_de_un_segundo() -> None:
    # RR alternado 0,9 / 1,1 s. Se borra un R precedido por 0,9 y seguido por
    # 1,1: el segmento del latido anterior llega hasta la mitad del hueco de
    # 2 s, pasa por encima del QRS perdido, y el delineador toma ese QRS como T.
    rr = np.tile([0.9, 1.1], 40)
    signal, rpeaks = _ecg_from_rr(rr)
    missed = 41
    assert rr[missed - 1] == 0.9 and rr[missed] == 1.1
    train = np.delete(rpeaks, missed)
    cleaned = clean_signal(signal, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)
    before, after = missed - 1, missed  # índices en el tren sin el R perdido

    # Sin los controles, el escenario del benchmark: QT de casi un segundo y
    # el latido contado como válido.
    loose = IntervalThresholds(
        qt_max_ms=5000.0, rr_max_s=10.0, rr_max_ratio=100.0, reject_truncated_t=False
    )
    unguarded = measure_beats(cleaned, signal, train, good, SAMPLE_RATE, loose)
    assert unguarded is not None
    assert unguarded.qt_ms[before] > 900.0
    assert unguarded.valid[before]

    beats = measure_beats(cleaned, signal, train, good, SAMPLE_RATE, DEFAULTS)
    full = measure_intervals(cleaned, signal, rpeaks, good, SAMPLE_RATE, DEFAULTS)
    result = measure_intervals(cleaned, signal, train, good, SAMPLE_RATE, DEFAULTS)

    assert beats is not None and full is not None and result is not None
    assert not beats.valid[before]  # QT falso: delineó sobre el QRS perdido
    assert not beats.valid[after]  # RR previo de 2 s: QTc falso
    assert result.qt_ms < DEFAULTS.qt_max_ms
    assert result.qt_ms == pytest.approx(full.qt_ms, abs=10.0)
    assert result.qtc_ms == pytest.approx(full.qtc_ms, abs=10.0)


def test_valid_es_la_conjuncion_de_los_controles_expuestos() -> None:
    signal, rpeaks, flags = _ectopy(every=5)
    cleaned = clean_signal(signal, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)

    beats = measure_beats(cleaned, signal, rpeaks, good, SAMPLE_RATE, DEFAULTS, dominant=~flags)

    assert beats is not None
    controls = (
        beats.candidate
        & beats.ordered
        & beats.qt_ok
        & beats.rr_ok
        & beats.peak_ok
        & beats.t_contained
        & beats.qrs_positive
        & beats.dominant_ok
    )
    assert np.array_equal(beats.valid, controls & np.isfinite(beats.qtc_ms))
    assert beats.rr_floor_s == pytest.approx(0.8 * 60.0 / 70.0, abs=0.01)
    assert beats.rr_cap_s == pytest.approx(1.5 * 60.0 / 70.0, abs=0.01)


# --------------------------------------------------------------------------- #
# Ectopia: el QT se mide en latidos sinusales
# --------------------------------------------------------------------------- #


def test_bigeminismo_no_reporta_el_qtc_de_los_ectopicos() -> None:
    # A 50 lpm el ventricular llega a 83 lpm, debajo del techo de FC. Con R
    # verdaderos y sin el piso de prematuridad, los latidos válidos del bloque
    # son los ventriculares (T invertida y ancha) y el QTc del bloque es el de
    # ellos: un `qtc_long` falso. Con el piso, ningún latido queda y sale None.
    signal, rpeaks, _ = _ectopy(every=2, bpm=50.0)
    sinus = _measure(*_ecg_from_rr(np.full(250, 60.0 / 50.0), noise_uv=15.0))
    no_prematurity = IntervalThresholds(rr_min_ratio=0.0, min_coverage_ratio=0.0)

    assert sinus is not None
    assert _measure(signal, rpeaks) is None
    unguarded = _measure(signal, rpeaks, thresholds=no_prematurity)
    assert unguarded is not None
    assert unguarded.qtc_ms > sinus.qtc_ms + 25.0
    # Y la FC que lo acompaña es la del acople del ectópico, no la del ritmo.
    assert unguarded.heart_rate_bpm > 1.5 * unguarded.candidate_heart_rate_bpm


def test_mascara_dominante_saca_al_ectopico_y_a_sus_vecinos() -> None:
    signal, rpeaks, flags = _ectopy(every=5)
    sinus = _measure(*_ecg_from_rr(np.full(350, 60.0 / 70.0), noise_uv=15.0))
    cleaned = clean_signal(signal, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)
    near_ectopic = flags | np.roll(flags, 1) | np.roll(flags, -1)

    without = measure_beats(cleaned, signal, rpeaks, good, SAMPLE_RATE, DEFAULTS)
    with_mask = measure_beats(cleaned, signal, rpeaks, good, SAMPLE_RATE, DEFAULTS, dominant=~flags)
    result = _measure(signal, rpeaks, dominant=~flags)

    assert without is not None and with_mask is not None
    assert result is not None and sinus is not None
    # Sin la máscara el piso de prematuridad saca al ectópico y al anterior,
    # pero el post-ectópico (pausa de 1,4 ciclos, debajo del techo de 1,5) entra.
    post_ectopic = np.roll(flags, 1)
    assert without.valid[post_ectopic].any()
    assert not without.valid[flags].any()
    # Con la máscara no entra ningún vecino de un ectópico y el QTc es el sinusal.
    assert not with_mask.valid[near_ectopic].any()
    assert result.qtc_ms == pytest.approx(sinus.qtc_ms, abs=3.0)


def test_trigeminismo_sin_mascara_no_reporta_la_mediana_de_los_post_ectopicos() -> None:
    # Un ventricular cada tres: el piso de prematuridad saca al ectópico y al
    # anterior, y solo quedan los post-ectópicos, un tercio de los candidatos.
    # Su mediana no representa el ritmo: la cobertura mínima devuelve None.
    signal, rpeaks, flags = _ectopy(every=3)
    cleaned = clean_signal(signal, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)
    relaxed = IntervalThresholds(min_coverage_ratio=0.0)

    beats = measure_beats(cleaned, signal, rpeaks, good, SAMPLE_RATE, relaxed)

    assert beats is not None
    assert np.array_equal(
        np.flatnonzero(beats.valid), np.flatnonzero(beats.valid & np.roll(flags, 1))
    )
    assert _measure(signal, rpeaks, thresholds=relaxed) is not None
    assert _measure(signal, rpeaks) is None


def test_fc_de_los_latidos_medidos_y_del_bloque() -> None:
    # 120 latidos a 75 lpm y 180 a 110: el techo de FC saca a los rápidos. La
    # FC que acompaña al QTc es la de los medidos; la del bloque, otra.
    rr = np.concatenate([np.full(120, 0.8), np.full(180, 0.546)])
    result = _measure(*_ecg_from_rr(rr), thresholds=IntervalThresholds(min_coverage_ratio=0.0))

    assert result is not None
    assert result.heart_rate_bpm == pytest.approx(75.0, abs=1.0)
    assert result.candidate_heart_rate_bpm == pytest.approx(110.0, abs=1.0)


def test_mascara_dominante_desalineada_levanta() -> None:
    synth = synth_ecg(60.0, bpm=60.0, firmware_lag_ms=0.0)
    cleaned = clean_signal(synth.signal_mv, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)
    short = np.ones(synth.rpeaks.size - 1, dtype=bool)

    with pytest.raises(ValueError):
        measure_intervals(
            cleaned, synth.signal_mv, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS, dominant=short
        )


# --------------------------------------------------------------------------- #
# El R tiene que estar sobre el QRS y el QRS tiene que ser positivo
# --------------------------------------------------------------------------- #


def test_r_fuera_del_pico_no_entra() -> None:
    # Lo que dejaba `correct_artifacts` al interpolar R con un ritmo irregular:
    # R que no caen en el pico del QRS. Uno de cada cuatro se corre 30 ms.
    signal, rpeaks = _regular(70.0)
    moved = np.zeros(rpeaks.size, dtype=bool)
    moved[2::4] = True
    train = rpeaks + np.where(moved, int(0.03 * SAMPLE_RATE), 0)
    cleaned = clean_signal(signal, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)

    beats = measure_beats(cleaned, signal, train, good, SAMPLE_RATE, DEFAULTS)

    assert beats is not None
    assert not beats.peak_ok[moved].any()
    assert not beats.valid[moved].any()
    assert beats.peak_ok[~moved].all()


def test_amplitud_r_se_mide_en_el_pico_y_no_en_el_indice_del_tren() -> None:
    # Un R corrido dos muestras sigue sobre el pico (dentro de la tolerancia) y
    # la amplitud sale igual: se mide donde `prominence` corrió el R, no en el
    # índice del tren (con el índice daba 0,91 en vez de 0,98).
    signal, _ = _regular(70.0, seconds=120.0)
    rpeaks = detect_rpeaks(clean_signal(signal, SAMPLE_RATE), SAMPLE_RATE)  # sobre el pico

    on_peak = _measure(signal, rpeaks)
    shifted = _measure(signal, rpeaks + 2)

    assert on_peak is not None and shifted is not None
    assert shifted.r_amplitude_mv == pytest.approx(on_peak.r_amplitude_mv)
    assert shifted.qt_ms == pytest.approx(on_peak.qt_ms)


def test_qrs_negativo_no_se_mide() -> None:
    # Señal invertida: el detector toma la S invertida como R y `prominence` pone
    # R_onset en el valle del QRS. El QT sale corto y la "amplitud R" mide S − R.
    signal, _ = _regular(60.0, seconds=120.0)
    inverted = (-signal).astype(np.float32)
    upright = _measure(signal, detect_rpeaks(clean_signal(signal, SAMPLE_RATE), SAMPLE_RATE))
    detected = detect_rpeaks(clean_signal(inverted, SAMPLE_RATE), SAMPLE_RATE)

    assert upright is not None
    assert _measure(inverted, detected) is None
    unguarded = _measure(
        inverted, detected, thresholds=IntervalThresholds(reject_negative_qrs=False)
    )
    assert unguarded is not None
    assert unguarded.qt_ms < upright.qt_ms - 20.0


# --------------------------------------------------------------------------- #
# Robustez: nada de lo que NeuroKit haga tumba el bloque
# --------------------------------------------------------------------------- #


def test_r_a_menos_de_90_ms_no_levanta_y_sus_latidos_quedan_afuera() -> None:
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)
    doubles = synth.rpeaks[::10] + int(0.04 * SAMPLE_RATE)  # dobles detecciones a 40 ms
    train = np.sort(np.concatenate([synth.rpeaks, doubles]))
    cleaned = clean_signal(synth.signal_mv, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)

    beats = measure_beats(cleaned, synth.signal_mv, train, good, SAMPLE_RATE, DEFAULTS)
    result = measure_intervals(cleaned, synth.signal_mv, train, good, SAMPLE_RATE, DEFAULTS)

    assert beats is not None and result is not None
    doubled = np.isin(beats.rpeaks, doubles)
    original_of_double = np.isin(beats.rpeaks, synth.rpeaks[::10])
    assert not beats.valid[doubled].any()
    assert not beats.valid[original_of_double].any()
    assert 300.0 <= result.qt_ms <= 450.0


def test_r_en_los_bordes_repetidos_o_contiguos_no_levanta() -> None:
    synth = synth_ecg(60.0, bpm=60.0, firmware_lag_ms=0.0)
    n = synth.signal_mv.size
    edges = np.array([-5, 0, 1, 2, n - 2, n - 1, n, n + 10], dtype=np.int64)
    contiguous = np.concatenate([synth.rpeaks[5:8] + 1, synth.rpeaks[10:12]])  # R+1 y repetidos
    train = np.concatenate([synth.rpeaks, edges, contiguous])

    result = _measure(synth.signal_mv, train)

    # El tren se sanea (muestra 0, fuera de rango, repetidos, R+1) y el resto
    # del bloque se mide igual.
    assert result is not None
    assert 300.0 <= result.qt_ms <= 450.0


def test_mascara_dominante_sigue_al_tren_saneado() -> None:
    # `dominant` viene alineado con el tren crudo; el saneo (repetidos, fuera de
    # rango, desorden) no puede correrla de latido.
    signal, rpeaks, flags = _ectopy(every=5)
    n = signal.size
    order = np.random.default_rng(5).permutation(rpeaks.size)
    train = np.concatenate([rpeaks[order], rpeaks[:3], [-4, n + 7]])
    dominant = np.concatenate([~flags[order], ~flags[:3], [True, True]])

    shuffled = _measure(signal, train, dominant=dominant)
    ordered = _measure(signal, rpeaks, dominant=~flags)

    assert shuffled is not None
    assert shuffled == ordered


# --------------------------------------------------------------------------- #
# `owned`: los latidos que informa el llamador
# --------------------------------------------------------------------------- #


def test_owned_limita_los_candidatos_sin_contagiarse_a_los_vecinos() -> None:
    # Lo que hace el pipeline con el contexto de un bloque: la señal y el tren
    # son los del bloque entero, pero se miden solo los latidos de la parte
    # nueva. El primero de la parte nueva entra con el R-R que le da el último
    # del contexto, y ningún otro control cambia.
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)
    cleaned = clean_signal(synth.signal_mv, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)
    owned = synth.rpeaks >= 60 * SAMPLE_RATE

    whole = measure_beats(cleaned, synth.signal_mv, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS)
    mine = measure_beats(
        cleaned, synth.signal_mv, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS, owned=owned
    )

    assert whole is not None and mine is not None
    assert np.array_equal(mine.candidate, whole.candidate & owned)
    assert np.array_equal(mine.valid, whole.valid & owned)
    assert mine.candidate[int(np.argmax(owned))]
    # Las dos mitades suman el todo: cada latido lo mide un solo llamador.
    first = _measure(synth.signal_mv, synth.rpeaks)
    halves = [
        measure_intervals(
            cleaned, synth.signal_mv, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS, owned=mask
        )
        for mask in (~owned, owned)
    ]
    assert first is not None and halves[0] is not None and halves[1] is not None
    assert halves[0].candidate_beats + halves[1].candidate_beats == first.candidate_beats


def test_owned_desalineado_levanta() -> None:
    synth = synth_ecg(60.0, bpm=60.0, firmware_lag_ms=0.0)
    cleaned = clean_signal(synth.signal_mv, SAMPLE_RATE)
    good = np.ones(cleaned.size, dtype=bool)
    short = np.ones(synth.rpeaks.size - 1, dtype=bool)

    with pytest.raises(ValueError, match="owned"):
        measure_intervals(
            cleaned, synth.signal_mv, synth.rpeaks, good, SAMPLE_RATE, DEFAULTS, owned=short
        )


@pytest.mark.parametrize("bpm", [30.0, 25.0, 20.0])
def test_frecuencia_muy_baja_no_levanta(bpm: float) -> None:
    synth = synth_ecg(240.0, bpm=bpm, firmware_lag_ms=0.0)

    result = _measure(synth.signal_mv, synth.rpeaks)

    # A 25 y 20 lpm el RR previo pasa del techo de 2 s: ningún latido es válido.
    if bpm < 30.0:
        assert result is None
    else:
        assert result is not None
        assert 300.0 <= result.qt_ms <= 450.0
        assert result.heart_rate_bpm == pytest.approx(30.0)


def test_senal_degenerada_no_levanta() -> None:
    n = 120 * SAMPLE_RATE
    rpeaks = np.arange(SAMPLE_RATE, n - SAMPLE_RATE, SAMPLE_RATE, dtype=np.int64)
    good = np.ones(n, dtype=bool)
    flat = np.zeros(n, dtype=np.float32)
    with_nan = flat.copy()
    with_nan[1000:5000] = np.nan

    assert measure_intervals(flat, flat, rpeaks, good, SAMPLE_RATE, DEFAULTS) is None
    assert measure_intervals(with_nan, with_nan, rpeaks, good, SAMPLE_RATE, DEFAULTS) is None
    empty = np.empty(0, dtype=np.int64)
    assert measure_intervals(flat, flat, empty, good, SAMPLE_RATE, DEFAULTS) is None


def test_excepcion_de_neurokit_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> dict[str, list[float]]:
        raise IndexError("index 0 is out of bounds")  # el crash de dwt a FC baja

    monkeypatch.setattr(intervals, "_prominence_delineator", lambda: explode)
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)

    assert _measure(synth.signal_mv, synth.rpeaks) is None


def test_salida_desalineada_de_neurokit_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lo que hace la API pública con un valor <= 0: lo saca y corre el resto.
    def shifted(ecg: np.ndarray, rpeaks: np.ndarray, sampling_rate: int) -> dict[str, list[float]]:
        onsets = [float(r - 10) for r in rpeaks[1:]]
        peaks = [float(r + 100) for r in rpeaks]
        offsets = [float(r + 150) for r in rpeaks]
        return {"ECG_R_Onsets": onsets, "ECG_T_Peaks": peaks, "ECG_T_Offsets": offsets}

    monkeypatch.setattr(intervals, "_prominence_delineator", lambda: shifted)
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)

    with pytest.raises(ValueError, match="ECG_R_Onsets"):
        intervals._delineate_aligned(synth.signal_mv.astype(np.float64), synth.rpeaks, SAMPLE_RATE)
    assert _measure(synth.signal_mv, synth.rpeaks) is None


def test_delineador_privado_de_neurokit_sigue_existiendo() -> None:
    # Si una actualización de neurokit2 lo mueve, esto rompe CI en vez de
    # apagar las mediciones en silencio (el bloque devolvería None).
    module = importlib.import_module("neurokit2.ecg.ecg_delineate")

    assert callable(module._prominence_ecg_delineator)


def test_delineador_privado_da_las_mismas_marcas_que_en_la_validacion() -> None:
    # Golden: todo el número validado en QTDB depende de los internos del
    # delineador privado (la segmentación en R ± RR/2, las ventanas de 100 y
    # 200 ms de `peak_prominences`). Si una versión de NeuroKit los cambia, las
    # marcas se mueven y esto rompe aunque la función siga existiendo. Señal
    # fija sin limpiar (no depende de `clean_signal`), marcas de NeuroKit 0.2.13.
    rr = np.array([0.8, 0.9, 0.75, 1.0, 0.85, 0.8, 0.95, 0.7, 0.9, 0.8])
    times = 0.5 + np.concatenate(([0.0], np.cumsum(rr)))
    n = int((times[-1] + 1.0) * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    signal = np.zeros(n)
    for i, center in enumerate(times):
        low, high = (
            max(0, int((center - 0.5) * SAMPLE_RATE)),
            min(n, int((center + 0.7) * SAMPLE_RATE)),
        )
        signal[low:high] += _beat(t[low:high] - center, rr[i - 1] if i > 0 else rr[0])
    signal += np.random.default_rng(7).normal(0.0, 0.01, n)
    rpeaks = np.round(times * SAMPLE_RATE).astype(np.int64)

    waves = intervals._delineate_aligned(signal, rpeaks, SAMPLE_RATE)

    assert rpeaks.tolist() == [250, 650, 1100, 1475, 1975, 2400, 2800, 3275, 3625, 4075, 4475]
    assert waves["ECG_R_Onsets"].tolist() == [
        235,
        634,
        1087,
        1460,
        1960,
        2385,
        2783,
        3261,
        3609,
        4059,
        4461,
    ]
    assert waves["ECG_T_Peaks"].tolist() == [
        368,
        768,
        1221,
        1588,
        2100,
        2519,
        2912,
        3401,
        3737,
        4193,
        4593,
    ]
    assert waves["ECG_T_Offsets"].tolist() == [
        415,
        818,
        1268,
        1638,
        2149,
        2567,
        2957,
        3446,
        3786,
        4239,
        4641,
    ]


def test_largos_distintos_levantan() -> None:
    signal = np.zeros(1000, dtype=np.float32)
    rpeaks = np.array([100, 600], dtype=np.int64)

    with pytest.raises(ValueError):
        measure_intervals(signal, signal[:-1], rpeaks, np.ones(1000, bool), SAMPLE_RATE, DEFAULTS)
    with pytest.raises(ValueError):
        measure_intervals(signal, signal, rpeaks, np.ones(999, bool), SAMPLE_RATE, DEFAULTS)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"qt_min_ms": 700.0},
        {"min_beats": 0},
        {"rr_max_ratio": 1.0},
        {"rr_min_ratio": 1.0},
        {"max_heart_rate_bpm": 25.0},
        {"peak_tolerance_ms": -1.0},
        {"t_peak_margin_ms": -1.0},
        {"min_coverage_ratio": 1.5},
    ],
)
def test_umbrales_incoherentes_levantan(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        IntervalThresholds(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Qué se reporta
# --------------------------------------------------------------------------- #


def test_qrs_no_se_reporta_y_la_medicion_es_experimental() -> None:
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)

    result = _measure(synth.signal_mv, synth.rpeaks)

    assert result is not None
    assert result.qrs_ms is None
    assert result.method == "prominence"
    assert result.experimental is True


def test_amplitud_r_sale_de_raw_for_amplitude_y_no_de_la_senal_limpia() -> None:
    synth = synth_ecg(120.0, bpm=60.0, firmware_lag_ms=0.0)
    raw = synth.signal_mv
    cleaned = clean_signal(raw, SAMPLE_RATE)

    on_raw = _measure(raw, synth.rpeaks)
    on_double = _measure(raw, synth.rpeaks, amplitude_from=2.0 * raw)
    on_cleaned = _measure(raw, synth.rpeaks, amplitude_from=cleaned)

    assert on_raw is not None and on_double is not None and on_cleaned is not None
    # La delineación sale siempre de `cleaned`: solo cambia la amplitud.
    assert on_double.qt_ms == on_raw.qt_ms == on_cleaned.qt_ms
    assert on_double.r_amplitude_mv == pytest.approx(2.0 * on_raw.r_amplitude_mv)
    # R sintético de 1 mV sobre una Q de −0,15 mV.
    assert 0.85 <= on_raw.r_amplitude_mv <= 1.2
    # El pasabajos de la limpieza aplana el R (en QTDB, ~19 %).
    assert on_cleaned.r_amplitude_mv < 0.9 * on_raw.r_amplitude_mv
