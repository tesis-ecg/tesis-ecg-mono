"""Onda P, PR y hallazgos de conducción (`app/ml/conduction.py`).

Señal sintética con las ondas P y los QRS en tiempos independientes
(`tests/ecg_synth.synth_conduction`), analizada por el pipeline completo: lo que
se verifica es lo que persiste el motor, no una función suelta.
"""

import numpy as np
import pytest

from app.core.config import settings
from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml import beat_labels as bl
from app.ml import conduction, pipeline
from app.ml.hrv import build_rr, expected_rr
from app.ml.qrs import delineate_qrs
from app.ml.rpeak_detection import clean_signal
from tests.ecg_synth import SAMPLE_RATE, SynthResult, synth_conduction

DURATION = 150.0


def _analyze(ecg: SynthResult) -> pipeline.PipelineResult:
    config = pipeline.build_config(settings, SAMPLE_RATE)
    return pipeline.analyze_batch(
        ecg.signal_mv,
        ecg.flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        fold_key="conduccion",
    )


def _kinds(result: pipeline.PipelineResult, prefix: str = "") -> list[str]:
    return [f.kind for f in result.findings if f.kind.startswith(prefix)]


def _regular(p_to_r_s: float, **kwargs) -> pipeline.PipelineResult:
    beats = [1.0 + k for k in range(140)]
    return _analyze(synth_conduction(beats, duration_s=DURATION, p_to_r_s=p_to_r_s, **kwargs))


def _pr(ecg: SynthResult) -> np.ndarray:
    cleaned = clean_signal(ecg.signal_mv, SAMPLE_RATE)
    analyzable = np.ones(ecg.signal_mv.size, dtype=bool)
    qrs = delineate_qrs(ecg.signal_mv, ecg.rpeaks, analyzable, SAMPLE_RATE)
    waves = conduction.measure_p_waves(
        cleaned, ecg.rpeaks, qrs, np.ones(ecg.rpeaks.size, dtype=bool), SAMPLE_RATE
    )
    return waves.pr_ms(qrs, SAMPLE_RATE)


def _direct(ecg: SynthResult) -> conduction.ConductionInput:
    """Las reglas de conducción sobre los R **verdaderos**, sin detector ni gate.

    El detector de R de NeuroKit toma por QRS las ondas P que quedan solas en un
    R-R largo, y el gate marca esas ventanas como dudosas: con un bloqueo
    completo las reglas se ejercitan mal de punta a punta con una señal sintética.
    Acá se aíslan: la señal es la del generador, los R son los suyos y todo es GOOD.
    """
    cleaned = clean_signal(ecg.signal_mv, SAMPLE_RATE)
    analyzable = np.ones(ecg.signal_mv.size, dtype=bool)
    qrs = delineate_qrs(ecg.signal_mv, ecg.rpeaks, analyzable, SAMPLE_RATE)
    rr = build_rr(ecg.rpeaks, analyzable, SAMPLE_RATE)
    normal = np.ones(ecg.rpeaks.size, dtype=bool)
    candidates = normal.copy()
    candidates[0] = False
    response = conduction.p_filter(cleaned, SAMPLE_RATE)
    return conduction.ConductionInput(
        rpeaks=ecg.rpeaks,
        is_normal=normal,
        qrs=qrs,
        p=conduction.measure_p_waves(cleaned, ecg.rpeaks, qrs, candidates, SAMPLE_RATE, response),
        contiguous=rr.valid,
        expected_rr=expected_rr(rr),
        dominant=normal,
        owned=normal,
        cleaned=cleaned,
        response=response,
        analyzable=analyzable,
        origin=0,
        sample_rate=SAMPLE_RATE,
        classifier="beats-test",
    )


def test_el_pr_sigue_a_la_onda_p_y_no_se_inventa_sin_ella() -> None:
    """Mover la P 60 ms hacia atrás agranda el PR en ~60 ms; sin P no hay PR."""
    beats = [1.0 + k for k in range(40)]
    base = _pr(synth_conduction(beats, duration_s=42.0, p_to_r_s=0.16))
    late = _pr(synth_conduction(beats, duration_s=42.0, p_to_r_s=0.22))
    absent = _pr(synth_conduction(beats, duration_s=42.0, p_to_r_s=None))

    assert np.isfinite(base[1:]).all()
    assert 140 <= np.median(base[1:]) <= 225
    assert np.median(late[1:]) - np.median(base[1:]) == pytest.approx(60.0, abs=14.0)
    assert not np.isfinite(absent).any()


def test_una_p_pegada_a_la_t_anterior_no_se_busca() -> None:
    """A más de ~100 lpm la P cae sobre la T del latido anterior: sin PR, sin adivinar."""
    beats = list(np.arange(40) * 0.5 + 1.0)  # 120 lpm
    pr = _pr(synth_conduction(beats, duration_s=22.0, p_to_r_s=0.16))

    assert not np.isfinite(pr).any()


def test_un_pr_normal_no_produce_hallazgo_de_conduccion() -> None:
    result = _regular(0.16)

    assert not [f for f in result.findings if f.event_type is ECGEventType.CONDUCTION]
    assert np.nanmedian(bl.pr_ms(result.beat_labels, SAMPLE_RATE)) == pytest.approx(180, abs=30)


def test_un_pr_largo_sostenido_es_bav_de_primer_grado() -> None:
    result = _regular(0.28)

    [block] = [f for f in result.findings if f.kind == "av_block_first_degree"]
    assert block.event_type is ECGEventType.CONDUCTION
    assert block.severity is ECGEventSeverity.LOW
    assert block.alert_message is None  # LOW: sin aviso
    assert float(block.metadata["prMs"]) > 200
    # Dura todo el registro analizado, no un instante.
    assert block.length_samples / SAMPLE_RATE > 100
    assert np.nanmedian(bl.pr_ms(result.beat_labels, SAMPLE_RATE)) > 250


def test_un_pr_corto_sostenido_es_un_hallazgo_de_pr_corto() -> None:
    """La regla, con un PR de 100 ms medido en cada latido.

    El detector de P no ve ondas a menos de ~46 ms del inicio del QRS (la
    respuesta del filtro de P se mezcla con la subida del complejo), así que con
    señal sintética el PR más corto que sale medido es ~130 ms: la regla se
    ejercita con el PR ya medido y no con la señal. Ver `docs/deteccion-ml`.
    """
    beats = np.array([(1.0 + k) * SAMPLE_RATE for k in range(60)], dtype=np.int64)
    qrs = delineate_qrs(
        synth_conduction(list(1.0 + np.arange(60)), duration_s=62.0).signal_mv,
        beats,
        np.ones(62 * SAMPLE_RATE, dtype=bool),
        SAMPLE_RATE,
    )
    onset = qrs.onset.astype(np.int64) - 50  # 100 ms antes del inicio del QRS
    waves = conduction.PWaves(
        onset=onset,
        peak=onset + 25,
        valid=np.ones(beats.size, dtype=bool),
        height=np.full(beats.size, 0.05, dtype=np.float32),
    )
    flat = np.zeros(62 * SAMPLE_RATE, dtype=np.float32)
    normal = np.ones(beats.size, dtype=bool)

    found = conduction.conduction_findings(
        conduction.ConductionInput(
            rpeaks=beats,
            is_normal=normal,
            qrs=qrs,
            p=waves,
            contiguous=np.ones(beats.size - 1, dtype=bool),
            expected_rr=np.r_[np.nan, np.ones(beats.size - 1)].astype(np.float32),
            dominant=normal,
            owned=normal,
            cleaned=flat,
            response=flat,
            analyzable=np.ones(flat.size, dtype=bool),
            origin=0,
            sample_rate=SAMPLE_RATE,
            classifier="beats-test",
        )
    )

    [short] = [f for f in found if f.kind == "short_pr"]
    assert short.severity is ECGEventSeverity.LOW
    assert float(short.metadata["prMs"]) == pytest.approx(
        100.0 + 2000 / SAMPLE_RATE * 0 + 0, abs=60
    )
    assert float(short.metadata["prMs"]) < 120


def test_un_pr_que_oscila_al_azar_no_se_informa() -> None:
    """Una P a medias da un PR sin mediana que valga: la dispersión lo descarta."""
    rng = np.random.default_rng(3)
    lead = [float(x) for x in rng.uniform(0.10, 0.34, size=140)]
    result = _analyze(
        synth_conduction([1.0 + k for k in range(140)], duration_s=DURATION, p_to_r_s=lead)
    )

    assert "av_block_first_degree" not in _kinds(result)
    assert "short_pr" not in _kinds(result)


def test_sin_onda_p_no_hay_hallazgos_de_pr() -> None:
    result = _regular(None)  # type: ignore[arg-type]

    assert not np.isfinite(bl.pr_ms(result.beat_labels, SAMPLE_RATE)).any()
    assert not _kinds(result, "av_block")
    assert "short_pr" not in _kinds(result)


def test_un_qrs_ancho_en_la_dominante_es_un_posible_bloqueo_de_rama() -> None:
    """Todos los latidos son anchos: no hay V (la dominante lo es), hay QRS ancho sostenido."""
    beats = [1.0 + k for k in range(140)]
    result = _analyze(synth_conduction(beats, duration_s=DURATION, wide=frozenset(range(140))))

    [block] = [f for f in result.findings if f.kind == "wide_qrs_sustained"]
    assert block.severity is ECGEventSeverity.MEDIUM
    assert float(block.metadata["qrsMs"]) >= 120
    labels = {chr(code) for code in result.beat_labels["label"]}
    assert "V" not in labels
    assert "ventricular_ectopy" not in _kinds(result)


def test_mobitz_2_es_un_latido_caido_con_pr_constante() -> None:
    """P cada 1,0 s con PR fijo y un QRS que falta cada seis: Mobitz II, HIGH, con aviso."""
    beats = [1.0 + k for k in range(140) if k % 6 != 5]
    dropped = [1.0 + k - 0.16 for k in range(140) if k % 6 == 5]
    result = _analyze(synth_conduction(beats, duration_s=DURATION, extra_p_s=dropped))

    blocks = [f for f in result.findings if f.kind == "av_block_mobitz_2"]
    assert blocks
    assert all(f.severity is ECGEventSeverity.HIGH for f in blocks)
    assert all(f.alert_message and "posible" in f.alert_message for f in blocks)
    assert all(f.push for f in blocks)  # la P bloqueada es tan clara como las conducidas
    assert "av_block_mobitz_1" not in _kinds(result)


def test_mobitz_1_es_un_pr_que_se_alarga_hasta_caer_un_latido() -> None:
    beats, leads, dropped = [], [], []
    for k in range(140):
        phase = k % 4
        if phase == 3:
            dropped.append(1.0 + k)
            continue
        lead = (0.16, 0.22, 0.28)[phase]
        beats.append(1.0 + k + lead)
        leads.append(lead)
    result = _analyze(
        synth_conduction(beats, duration_s=DURATION, p_to_r_s=leads, extra_p_s=dropped)
    )

    blocks = [f for f in result.findings if f.kind == "av_block_mobitz_1"]
    assert blocks
    assert all(f.severity is ECGEventSeverity.MEDIUM for f in blocks)
    assert float(blocks[0].metadata["nextPrMs"]) < float(blocks[0].metadata["priorPrMs"]) - 15
    assert "av_block_mobitz_2" not in _kinds(result)


def test_un_latido_caido_sin_p_clara_no_es_un_bloqueo() -> None:
    """Falta el QRS pero no hay P donde el ritmo la espera: es una pausa, no un BAV."""
    beats = [1.0 + k for k in range(140) if k % 6 != 5]
    result = _analyze(synth_conduction(beats, duration_s=DURATION))

    assert not _kinds(result, "av_block_mobitz")


def test_ondas_p_regulares_e_independientes_de_un_qrs_lento_son_un_posible_bav_completo() -> None:
    beats = [1.0 + 1.3 * k for k in range(110)]
    atrial = [0.5 + 0.83 * k for k in range(180)]
    ecg = synth_conduction(beats, duration_s=DURATION, p_to_r_s=None, extra_p_s=atrial)

    blocks = [
        f for f in conduction.conduction_findings(_direct(ecg)) if f.kind == "av_block_complete"
    ]

    assert blocks
    assert all(f.severity is ECGEventSeverity.CRITICAL for f in blocks)
    assert all(f.alert_message and "posible" in f.alert_message for f in blocks)
    assert float(blocks[0].metadata["ventricularBpm"]) < 50
    assert float(blocks[0].metadata["atrialBpm"]) > float(blocks[0].metadata["ventricularBpm"])
    # Las ondas auriculares quedan concentradas en un período común.
    assert float(blocks[0].metadata["pLock"]) > 0.85
    # La guarda de P del push: ondas visibles suficientes y muy regulares.
    guarded = (
        int(blocks[0].metadata["pWaves"]) >= conduction.COMPLETE_PUSH_MIN_P
        and float(blocks[0].metadata["pLock"]) >= conduction.COMPLETE_PUSH_P_LOCK
    )
    assert blocks[0].push is guarded


def test_un_bloqueo_2_a_1_con_pr_constante_no_es_completo() -> None:
    """Un QRS cada dos P, siempre en la misma fase: conducción 2:1, no disociación."""
    atrial = [0.5 + 0.65 * k for k in range(230)]
    beats = [atrial[k] + 0.16 for k in range(0, 230, 2)]
    ecg = synth_conduction(beats, duration_s=DURATION, p_to_r_s=None, extra_p_s=atrial)

    found = conduction.conduction_findings(_direct(ecg))

    assert "av_block_complete" not in [f.kind for f in found]


def test_un_ritmo_regular_y_lento_no_basta_sin_ondas_p_independientes() -> None:
    """Bradicardia sinusal con su P de cada latido: nada que disociar."""
    beats = [1.0 + 1.3 * k for k in range(110)]
    ecg = synth_conduction(beats, duration_s=DURATION, p_to_r_s=0.16)

    assert not [f for f in conduction.conduction_findings(_direct(ecg)) if f.kind.startswith("av_")]


def test_un_ritmo_lento_con_su_p_de_cada_latido_no_es_bloqueo() -> None:
    beats = [1.0 + 1.3 * k for k in range(110)]
    result = _analyze(synth_conduction(beats, duration_s=DURATION, p_to_r_s=0.16))

    assert not _kinds(result, "av_block")
