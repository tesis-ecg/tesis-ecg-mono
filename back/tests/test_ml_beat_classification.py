"""Clasificación por latido (N, S, V, X, Q) dentro del pipeline completo.

Cada regla de `app/ml/beat_classification.py` con una señal sintética
determinista (`tests/ecg_synth.py`): lo que se mira son las etiquetas que el
motor persiste (`PipelineResult.beat_labels`) y los hallazgos que salen de ellas.
"""

import collections
import statistics
import time
from dataclasses import replace

import numpy as np
import pytest

from app.core.config import settings
from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml import beat_labels as bl
from app.ml import pipeline
from app.ml.contracts import Finding
from app.ml.decompression import FLAG_LEAD_OFF
from tests.ecg_synth import SAMPLE_RATE, SynthResult, synth_conduction, synth_ecg


def _config(**overrides: object) -> pipeline.PipelineConfig:
    config = pipeline.build_config(settings, SAMPLE_RATE)
    return replace(config, **overrides) if overrides else config  # type: ignore[arg-type]


def _analyze(
    ecg: SynthResult,
    config: pipeline.PipelineConfig | None = None,
    **kwargs: object,
) -> pipeline.PipelineResult:
    config = config or _config()
    return pipeline.analyze_batch(
        ecg.signal_mv,
        ecg.flags,
        start_sample_index=int(kwargs.pop("start_sample_index", 0)),  # type: ignore[call-overload]
        bank=kwargs.pop("bank", None) or pipeline.empty_bank(config),  # type: ignore[arg-type]
        config=config,
        fold_key=str(kwargs.pop("fold_key", "clasificacion")),
        **kwargs,  # type: ignore[arg-type]
    )


def _count(result: pipeline.PipelineResult) -> collections.Counter[str]:
    assert result.beat_labels is not None
    return collections.Counter(chr(code) for code in result.beat_labels["label"])


def _by_sample(result: pipeline.PipelineResult) -> dict[int, str]:
    assert result.beat_labels is not None
    return {int(record["sample_index"]): chr(record["label"]) for record in result.beat_labels}


def _kinds(result: pipeline.PipelineResult) -> list[str]:
    return [finding.kind for finding in result.findings]


def _premature(count: int, *, pairs: bool = False) -> list[float]:
    """R a 1,0 s con un latido prematuro (al 70 %) cada `count`, y su pausa compensatoria."""
    times, now = [], 1.0
    index = 0
    while now < 146.0:
        times.append(now)
        index += 1
        if index % count == 0:
            gap = 0.7
            times.append(now + gap)
            if pairs:
                times.append(now + gap + 0.7)
                now += gap + 0.7 + 1.0 + 0.6
            else:
                now += gap + 1.3
        else:
            now += 1.0
    return times


def test_un_ecg_limpio_es_todo_normal_y_no_produce_hallazgos_de_ectopia() -> None:
    result = _analyze(synth_ecg(duration_s=300.0))

    counts = _count(result)
    assert counts["N"] >= 295 and counts["V"] == counts["S"] == 0
    assert not [f for f in result.findings if f.event_type in (ECGEventType.ECTOPY,)]
    # Cada latido del lote tiene su etiqueta y su QRS medido.
    labelled = result.beat_labels
    assert labelled is not None
    assert (labelled["evidence"] & bl.EVIDENCE_QRS_MEASURED).all()
    assert np.nanmedian(bl.qrs_ms(labelled, SAMPLE_RATE)) == pytest.approx(92, abs=14)
    # `sample_index` es absoluto al estudio: acá el lote arranca en 0.
    assert int(labelled["sample_index"][0]) < 2 * SAMPLE_RATE


def test_el_lote_que_arranca_mas_adelante_etiqueta_en_coordenadas_del_estudio() -> None:
    ecg = synth_ecg(duration_s=120.0)
    offset = 1_000_000

    shifted = _analyze(ecg, start_sample_index=offset)
    base = _analyze(ecg)

    assert shifted.beat_labels is not None and base.beat_labels is not None
    assert np.array_equal(
        shifted.beat_labels["sample_index"] - offset, base.beat_labels["sample_index"]
    )


def test_un_foco_ventricular_se_etiqueta_v_latido_a_latido() -> None:
    ecg = synth_ecg(duration_s=300.0, ectopic_every=12)

    result = _analyze(ecg)

    by_sample = _by_sample(result)
    ectopic = {int(peak) for peak in ecg.ectopic_peaks}
    labelled_v = {sample for sample, label in by_sample.items() if label == "V"}
    # Todo V está en un ectópico del generador, y no se escapa ninguno.
    assert labelled_v <= {s for s in by_sample if any(abs(s - e) <= 5 for e in ectopic)}
    assert len(labelled_v) == len(ectopic)
    assert _count(result)["S"] == 0
    # Aisladas: cuentan en las métricas, no producen hallazgo de episodio.
    assert "ventricular_ectopy" not in _kinds(result)


def test_un_par_de_v_es_un_episodio_de_ectopia_ventricular_y_no_una_anomalia_aparte() -> None:
    times = [1.0 + k for k in range(140)]
    ecg = synth_conduction(times, duration_s=150.0, wide=frozenset({40, 41, 90, 91}))

    result = _analyze(ecg)

    episodes = [f for f in result.findings if f.kind == "ventricular_ectopy"]
    assert len(episodes) == 2
    assert all(f.event_type is ECGEventType.ECTOPY for f in episodes)
    assert all(f.severity is ECGEventSeverity.MEDIUM for f in episodes)  # un par
    assert all(f.beat_count == 2 for f in episodes)
    assert all(f.score is not None and f.score > 0 for f in episodes)
    # Los mismos latidos no se pintan dos veces como morfología atípica.
    assert not [f for f in result.findings if f.kind == "morphology_anomaly"]
    # Y el clasificador no inventó ectópicas de más.
    assert _count(result)["V"] == 4


def test_un_latido_prematuro_con_qrs_angosto_y_forma_normal_es_supraventricular() -> None:
    times = _premature(8)
    ecg = synth_conduction(times, duration_s=150.0)

    result = _analyze(ecg)

    counts = _count(result)
    prematures = sum(1 for k in range(1, len(times)) if times[k] - times[k - 1] < 0.8)
    assert counts["S"] == prematures
    assert counts["V"] == 0
    flagged = result.beat_labels
    assert flagged is not None
    s_records = flagged[flagged["label"] == bl.CODE_S]
    assert (s_records["evidence"] & bl.EVIDENCE_PREMATURE).all()
    # La pausa compensatoria la trae todo prematuro que tiene un latido después.
    assert (s_records["evidence"][:-1] & bl.EVIDENCE_COMPENSATORY).all()
    assert not (s_records["evidence"] & bl.EVIDENCE_WIDE).any()


def test_una_corrida_de_s_a_mas_de_100_lpm_es_una_posible_tps_sin_aviso() -> None:
    times = [1.0 + k for k in range(20)]
    # Cinco latidos supraventriculares seguidos a 0,45 s (133 lpm).
    run = [21.0 + 0.45 * k for k in range(1, 6)]
    after = [run[-1] + 1.0 + k for k in range(120)]
    ecg = synth_conduction([*times, *run, *after], duration_s=170.0)

    result = _analyze(ecg)

    [tps] = [f for f in result.findings if f.kind == "svt_run"]
    assert tps.event_type is ECGEventType.TACHYCARDIA
    assert tps.severity is ECGEventSeverity.MEDIUM
    assert tps.alert_message is None  # la TPS no sostenida no avisa
    assert tps.beat_count >= 3


def test_una_corrida_ventricular_a_mas_de_100_lpm_es_una_posible_tpv_con_aviso() -> None:
    times = [1.0 + k for k in range(25)]
    run = [26.0 + 0.45 * k for k in range(1, 7)]
    after = [run[-1] + 1.0 + k for k in range(110)]
    wide = frozenset(range(len(times), len(times) + len(run)))
    ecg = synth_conduction([*times, *run, *after], duration_s=170.0, wide=wide)

    result = _analyze(ecg)

    [tpv] = [f for f in result.findings if f.kind == "vt_run"]
    assert tpv.severity is ECGEventSeverity.HIGH
    assert tpv.alert_message is not None and "posible" in tpv.alert_message
    # Los R los confirma el detector del firmware: puede avisar al paciente.
    assert tpv.push is True
    assert tpv.metadata["sustained"] == 0
    # La corrida no se repite como episodio de ectopia sobre los mismos latidos.
    assert "ventricular_ectopy" not in _kinds(result)


def test_la_tpv_sin_confirmacion_del_firmware_alerta_pero_no_manda_push() -> None:
    times = [1.0 + k for k in range(25)]
    run = [26.0 + 0.45 * k for k in range(1, 7)]
    after = [run[-1] + 1.0 + k for k in range(110)]
    wide = frozenset(range(len(times), len(times) + len(run)))
    ecg = synth_conduction([*times, *run, *after], duration_s=170.0, wide=wide)
    sin_firmware = replace(ecg, flags=np.zeros_like(ecg.flags))

    [tpv] = [f for f in _analyze(sin_firmware).findings if f.kind == "vt_run"]

    assert tpv.alert_message is not None
    assert tpv.push is False


def test_las_corridas_fundidas_recalculan_su_severidad_con_la_duracion() -> None:
    """Dos corridas a 8 s se funden en una de 36 s, sostenida: pasa a CRITICAL."""
    sample_rate = SAMPLE_RATE
    one = Finding(
        kind="vt_run",
        event_type=ECGEventType.TACHYCARDIA,
        severity=ECGEventSeverity.HIGH,
        start_sample=0,
        length_samples=14 * sample_rate,
        dedupe_key="vt_run:0",
        beat_count=30,
        alert_message="x",
        push=False,
        beat_samples=tuple(range(0, 14 * sample_rate, 200)),
    )
    two = replace(one, start_sample=22 * sample_rate, dedupe_key="vt_run:11000", beat_samples=())
    config = _config()

    merged = pipeline._merge_classification([], [one, two], config=config)

    [fused] = [f for f in merged if f.kind == "vt_run"]
    assert fused.length_samples / sample_rate >= 30
    assert fused.severity is ECGEventSeverity.CRITICAL
    assert fused.push is True


def test_con_la_dominante_ancha_un_qrs_ancho_no_vuelve_ventricular_a_nadie() -> None:
    """Un paciente con bloqueo de rama tiene todos sus latidos anchos: sin V."""
    times = [1.0 + k for k in range(140)]
    ecg = synth_conduction(times, duration_s=150.0, wide=frozenset(range(140)))

    result = _analyze(ecg)

    counts = _count(result)
    assert counts["V"] == 0 and counts["N"] >= 135
    assert "wide_qrs_sustained" in _kinds(result)


def test_el_ruido_deja_los_latidos_en_x_y_nunca_los_clasifica() -> None:
    ecg = synth_ecg(duration_s=120.0)
    flags = ecg.flags.copy()
    flags[30 * SAMPLE_RATE : 70 * SAMPLE_RATE] |= FLAG_LEAD_OFF  # electrodo despegado
    result = _analyze(replace(ecg, flags=flags))

    by_sample = _by_sample(result)
    inside = [
        label for sample, label in by_sample.items() if 32 * SAMPLE_RATE < sample < 68 * SAMPLE_RATE
    ]
    outside = [label for sample, label in by_sample.items() if sample < 25 * SAMPLE_RATE]
    assert inside and set(inside) == {"X"}
    assert set(outside) <= {"N", "Q"}
    assert result.beat_labels is not None
    x_records = result.beat_labels[result.beat_labels["label"] == bl.CODE_X]
    # Un X no trae medidas: no se inventa un QRS en señal que no se leyó.
    assert (x_records["qrs_onset"] == bl.NOT_MEASURED).all()
    assert (x_records["p_onset"] == bl.NOT_MEASURED).all()


def test_un_latido_sin_ventana_completa_en_senal_buena_es_no_clasificable() -> None:
    """Un R a 400 ms del inicio tiene sus ±250 ms (N); si el detector ve uno más
    cerca del borde, no los tiene y es Q: ni N ni S ni V."""
    ecg = synth_conduction([0.4, *[1.5 + k for k in range(60)]], duration_s=64.0)
    edge = synth_conduction([0.2, *[1.5 + k for k in range(60)]], duration_s=64.0)

    result, near_edge = _analyze(ecg), _analyze(edge)

    assert _by_sample(result)[int(ecg.rpeaks[0])] == "N"
    assert _by_sample(near_edge).get(int(edge.rpeaks[0]), "Q") in {"Q", "X"}
    assert _count(near_edge)["N"] >= 55


def test_con_la_clasificacion_apagada_no_hay_etiquetas_ni_hallazgos_de_ectopia() -> None:
    ecg = synth_ecg(duration_s=300.0, ectopic_every=4)

    result = _analyze(ecg, _config(beats=None))

    assert result.beat_labels is None
    assert not {"ventricular_ectopy", "vt_run", "svt_run"} & set(_kinds(result))
    assert (
        pipeline.build_config(
            settings.model_copy(update={"ml_beat_classification_enabled": False}), SAMPLE_RATE
        ).beats
        is None
    )


def test_cada_latido_se_etiqueta_en_un_solo_bloque() -> None:
    """Dos bloques consecutivos con contexto y contexto derecho: ni se pisan ni se pierden."""
    ecg = synth_ecg(duration_s=240.0, ectopic_every=9, seed=11)
    config = _config()
    border, context, lookahead = 120 * SAMPLE_RATE, 60 * SAMPLE_RATE, 30 * SAMPLE_RATE

    first = pipeline.analyze_batch(
        ecg.signal_mv[: border + lookahead],
        ecg.flags[: border + lookahead],
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        fold_key="a",
        lookahead_samples=lookahead,
    )
    start = border - context
    second = pipeline.analyze_batch(
        ecg.signal_mv[start:],
        ecg.flags[start:],
        start_sample_index=start,
        bank=first.bank,
        config=config,
        fold_key="b",
        context_samples=context,
    )
    whole = _analyze(ecg)

    a, b = _by_sample(first), _by_sample(second)
    assert not set(a) & set(b)
    merged = {**a, **b}
    # Salvo los últimos 250 ms del registro, el mismo conjunto y las mismas etiquetas.
    reference = _by_sample(whole)
    assert set(merged) == set(reference)
    assert merged == reference


@pytest.mark.slow
def test_la_clasificacion_cuesta_menos_de_un_30_por_ciento_mas() -> None:
    """Un bloque de producción (300 s con 60 s de contexto y 30 s de contexto derecho),
    con y sin clasificar. Mediana de tres rondas de cinco corridas intercaladas."""
    ecg = synth_ecg(duration_s=390.0, ectopic_every=12, bpm=70.0)
    enabled, disabled = _config(), _config(beats=None)

    def seconds(config: pipeline.PipelineConfig) -> float:
        started = time.perf_counter()
        _analyze(ecg, config, context_samples=60 * SAMPLE_RATE, lookahead_samples=30 * SAMPLE_RATE)
        return time.perf_counter() - started

    seconds(enabled)
    seconds(disabled)
    ratios = []
    for _ in range(3):
        with_ = [seconds(enabled) for _ in range(5)]
        without = [seconds(disabled) for _ in range(5)]
        ratios.append(statistics.median(with_) / statistics.median(without))

    assert statistics.median(ratios) < 1.30


def test_un_r_falso_en_el_ruido_no_vuelve_prematuro_al_latido_que_le_sigue() -> None:
    """El R-R que trae al latido cruza señal que no es GOOD: no mide prematuridad.

    Un R detectado en un tramo de electrodo despegado (acá un QRS a 69,85 s,
    dentro del tramo marcado) deja al primer latido bueno a 0,45 s de él. Ese
    intervalo no es válido, así que el latido no es S por haber llegado "antes".
    """
    times = sorted([1.3 + k for k in range(140)] + [69.85])
    ecg = synth_conduction(times, duration_s=150.0)
    flags = ecg.flags.copy()
    flags[30 * SAMPLE_RATE : 70 * SAMPLE_RATE] |= FLAG_LEAD_OFF

    result = _analyze(replace(ecg, flags=flags))

    first_good = min(int(r) for r in ecg.rpeaks if r >= 70 * SAMPLE_RATE)
    assert _by_sample(result)[first_good] in {"N", "Q"}
    assert _count(result)["S"] == 0


def test_un_prematuro_con_qrs_ancho_nunca_es_supraventricular() -> None:
    """S pide QRS angosto: un prematuro ancho es V (forma distinta) o queda Q."""
    times = _premature(8)
    prematures = frozenset(k for k in range(1, len(times)) if times[k] - times[k - 1] < 0.8)
    ecg = synth_conduction(times, duration_s=150.0, wide=prematures)

    result = _analyze(ecg)

    by_sample = _by_sample(result)
    labels = [by_sample.get(int(ecg.rpeaks[k])) for k in sorted(prematures)]
    assert "S" not in labels
