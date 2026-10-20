"""Patrones de ectopia (`app/ml/ectopy.py`): aisladas, pares, corridas, bi y trigeminismo."""

import numpy as np
import pytest

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml import beat_labels as bl
from app.ml.contracts import EpisodeBudget
from app.ml.ectopy import (
    KIND_SUPRAVENTRICULAR,
    KIND_SVT_RUN,
    KIND_VENTRICULAR,
    KIND_VT_RUN,
    alert_for_run,
    ectopy_findings,
    ectopy_patterns,
    run_severity,
)

SAMPLE_RATE = 500


def _labels(text: str) -> np.ndarray:
    return np.array([ord(char) for char in text], dtype=np.uint8)


def _patterns(text: str, target: str = "V", *, cuts: tuple[int, ...] = ()):
    labels = _labels(text)
    contiguous = np.ones(max(labels.size - 1, 0), dtype=bool)
    for cut in cuts:  # el tramo se corta entre `cut` y `cut + 1`
        contiguous[cut] = False
    return ectopy_patterns(labels, contiguous, target)  # type: ignore[arg-type]


def test_aisladas_pares_y_corridas_se_cuentan_por_separado() -> None:
    result = _patterns("NNVNNVVNNVVVNNVVVVNN")

    assert result.total == 10
    assert result.single == 1
    assert result.pairs == 1 and result.pair_beats == 2
    assert result.runs.tolist() == [[9, 11], [14, 17]]
    assert result.run_beats == 7


def test_bigeminismo_son_tres_ciclos_seguidos_de_normal_y_ectopico() -> None:
    result = _patterns("NNNVNVNVNNNN")

    assert result.bigeminy_episodes == 1
    # Un latido ectópico por ciclo: tres.
    assert result.bigeminy_beats == 3
    assert result.single == 3  # las aisladas del patrón cuentan como aisladas
    assert result.trigeminy_episodes == 0
    assert _patterns("NNVNVNNNN").bigeminy_episodes == 0  # dos ciclos no alcanzan


def test_trigeminismo_son_ciclos_n_n_x() -> None:
    result = _patterns("NNVNNVNNVNNN")

    assert result.trigeminy_episodes == 1
    assert result.trigeminy_beats == 3
    assert result.bigeminy_episodes == 0
    assert _patterns("NNVNNVNNN").trigeminy_episodes == 0


def test_un_ruido_o_un_no_clasificable_cortan_cualquier_patron() -> None:
    assert _patterns("NVNVXNVNV").bigeminy_episodes == 0  # la X rompe la cadena
    assert _patterns("NVNVQNVNV").bigeminy_episodes == 0
    assert _patterns("VVXVV").pairs == 2
    assert _patterns("VVXVV").runs.shape[0] == 0
    # Un hueco de señal entre dos latidos también corta (R-R inválido).
    assert _patterns("NVNVNVNV", cuts=(3,)).bigeminy_episodes == 0
    assert _patterns("VVVV", cuts=(1,)).pairs == 2


def test_s_y_v_se_cuentan_por_separado() -> None:
    labels = _labels("NSNVNSNVNSNV")
    link = np.ones(labels.size - 1, dtype=bool)

    s, v = ectopy_patterns(labels, link, "S"), ectopy_patterns(labels, link, "V")

    assert (s.total, v.total) == (3, 3)
    assert s.bigeminy_episodes == 0  # alternan con V: no son ciclos N, S
    assert ectopy_patterns(_labels(""), np.empty(0, dtype=bool), "V").total == 0


def _findings(text: str, times_s: list[float] | None = None, target_first: bool = True):
    labels = _labels(text)
    n = labels.size
    times = np.asarray(times_s if times_s is not None else np.arange(n) * 0.8, dtype=float)
    rpeaks = (times * SAMPLE_RATE).astype(np.int64)
    return ectopy_findings(
        rpeaks=rpeaks,
        labels=labels,
        contiguous=np.ones(n - 1, dtype=bool),
        owned=np.ones(n, dtype=bool),
        ventricular_scores=np.full(n, 0.6, dtype=np.float32),
        prematurity=np.full(n, 0.7, dtype=np.float32),
        firmware_peaks=rpeaks.copy(),
        firmware_tolerance=40,
        sample_rate=SAMPLE_RATE,
        budget=EpisodeBudget(
            refractory_seconds=10.0,
            gap_beats=3,
            min_beats=2,
            max_per_study=200,
            max_per_kind=50,
            score_floor=0.0,
        ),
        classifier="beats-test",
    )


def test_una_corrida_de_tres_o_mas_v_a_mas_de_100_lpm_es_una_posible_tpv() -> None:
    # Cinco V a 0,4 s (150 lpm) en medio de latidos normales.
    times = [*np.arange(0, 6) * 0.8, *(5 * 0.8 + 0.8 + np.arange(5) * 0.4), 9.6, 10.4]
    found = _findings("NNNNNNVVVVVNN", times)

    [run] = [f for f in found if f.kind == KIND_VT_RUN]
    assert run.event_type is ECGEventType.TACHYCARDIA
    assert run.severity is ECGEventSeverity.HIGH  # no sostenida
    assert run.beat_count == 5
    assert run.metadata["peakBpm"] == pytest.approx(150.0, rel=0.02)
    assert run.alert_message and "posible" in run.alert_message
    # Los R los confirma el firmware: puede avisar al paciente.
    assert run.push is True
    # No hay un segundo hallazgo de episodio sobre los mismos latidos.
    assert not [f for f in found if f.kind == KIND_VENTRICULAR]


def test_una_corrida_lenta_no_es_taquicardia() -> None:
    """Tres V a 80 lpm: ritmo idioventricular acelerado, no una TPV. Sigue siendo
    un episodio de ectopia ventricular."""
    found = _findings("NNVVVNN", [0, 0.8, 1.6, 2.35, 3.1, 3.9, 4.7])

    assert not [f for f in found if f.kind == KIND_VT_RUN]
    [episode] = [f for f in found if f.kind == KIND_VENTRICULAR]
    assert episode.event_type is ECGEventType.ECTOPY
    assert episode.severity is ECGEventSeverity.MEDIUM  # tiene pares


def test_la_tpv_sostenida_es_critica_y_la_tps_sostenida_es_alta() -> None:
    n = 90  # 90 latidos a 0,4 s = 36 s
    for text, kind, expected in (
        ("V" * n, KIND_VT_RUN, ECGEventSeverity.CRITICAL),
        ("S" * n, KIND_SVT_RUN, ECGEventSeverity.HIGH),
    ):
        [run] = [f for f in _findings(text, list(np.arange(n) * 0.4)) if f.kind == kind]
        assert run.severity is expected
        assert run.metadata["sustained"] == 1
        assert run.alert_message is not None

    assert run_severity(KIND_SVT_RUN, False) is ECGEventSeverity.MEDIUM
    assert run_severity(KIND_VT_RUN, False) is ECGEventSeverity.HIGH


def test_la_tps_no_sostenida_no_avisa_pero_la_tpv_si() -> None:
    assert alert_for_run(KIND_SVT_RUN, ECGEventSeverity.MEDIUM) is None
    assert alert_for_run(KIND_SVT_RUN, ECGEventSeverity.HIGH) is not None
    assert alert_for_run(KIND_VT_RUN, ECGEventSeverity.HIGH) is not None
    assert alert_for_run("ventricular_ectopy", ECGEventSeverity.HIGH) is None


def test_el_push_de_una_tpv_corta_exige_que_el_firmware_confirme_los_r() -> None:
    labels = _labels("NNNVVVVNN")
    times = np.array([0, 0.8, 1.6, 2.4, 2.8, 3.2, 3.6, 4.4, 5.2])
    rpeaks = (times * SAMPLE_RATE).astype(np.int64)
    common = dict(
        rpeaks=rpeaks,
        labels=labels,
        contiguous=np.ones(8, dtype=bool),
        owned=np.ones(9, dtype=bool),
        ventricular_scores=np.full(9, 0.6, dtype=np.float32),
        prematurity=np.full(9, 0.7, dtype=np.float32),
        firmware_tolerance=40,
        sample_rate=SAMPLE_RATE,
        budget=EpisodeBudget(10.0, 3, 2, 200, 50, 0.0),
        classifier="beats-test",
    )

    sin_firmware = ectopy_findings(firmware_peaks=np.empty(0, dtype=np.int64), **common)
    [run] = [f for f in sin_firmware if f.kind == KIND_VT_RUN]

    assert run.push is False  # alerta en la bandeja del médico, sin despertar a nadie
    assert run.alert_message is not None
    assert run.metadata["firmwareConfirmedPct"] == 0.0


def test_los_episodios_de_ectopicas_necesitan_dos_y_suben_con_pares_o_patrones() -> None:
    aisladas = _findings("NNVNNNNVNNNN")
    par = _findings("NNVVNNNNN")
    dos_cercanas = _findings("NNVNVNNNN")
    bigeminismo = _findings("NNVNVNVNVNNN")

    assert not [f for f in aisladas if f.kind == KIND_VENTRICULAR]  # solo cuentan en métricas
    assert [f.severity for f in par if f.kind == KIND_VENTRICULAR] == [ECGEventSeverity.MEDIUM]
    assert [f.severity for f in dos_cercanas if f.kind == KIND_VENTRICULAR] == [
        ECGEventSeverity.LOW
    ]
    assert [f.severity for f in bigeminismo if f.kind == KIND_VENTRICULAR] == [
        ECGEventSeverity.MEDIUM
    ]
    # S usa su propio tipo de hallazgo y se puntúa por prematuridad (1 − 0,7).
    [s] = [f for f in _findings("NNSSNN") if f.kind == KIND_SUPRAVENTRICULAR]
    assert s.score == pytest.approx(0.3, abs=1e-6)
    assert s.event_type is ECGEventType.ECTOPY


def test_un_grupo_sin_latidos_nuevos_no_se_vuelve_a_informar() -> None:
    labels = _labels("NNVVNNNN")
    n = labels.size
    owned = np.zeros(n, dtype=bool)
    owned[5:] = True  # el par quedó entero en el contexto: ya lo informó el bloque anterior

    found = ectopy_findings(
        rpeaks=(np.arange(n) * 0.8 * SAMPLE_RATE).astype(np.int64),
        labels=labels,
        contiguous=np.ones(n - 1, dtype=bool),
        owned=owned,
        ventricular_scores=np.full(n, 0.6, dtype=np.float32),
        prematurity=np.ones(n, dtype=np.float32),
        firmware_peaks=np.empty(0, dtype=np.int64),
        firmware_tolerance=40,
        sample_rate=SAMPLE_RATE,
        budget=EpisodeBudget(10.0, 3, 2, 200, 50, 0.0),
        classifier="beats-test",
    )

    assert found == []
    assert bl.CODE_V == ord("V")
