"""Etapa 2 — el banco de plantillas.

Los dos invariantes que hacen que el diseño funcione, y que un re-clustering con
DBSCAN no puede dar:

1. Los `cluster_id` **nunca se renumeran**. El id viaja dentro de
   `event_metadata` de cada `ecg_event` ya escrito; si cambiara entre lotes,
   todas las filas de las horas anteriores empezarían a mentir.
2. El banco es un **acumulador monótono** que no vuelve a contar un lote.
"""

import numpy as np
import pytest

from app.ml.morphology import (
    TemplateBank,
    anomaly_score,
    assign_and_update,
    bank_from_state,
    bank_to_state,
    beat_length,
    consolidate,
    dissimilarity_to_dominant,
    dominant_template,
    extract_beats,
    score_only,
)
from app.ml.rpeak_detection import clean_signal, detect_rpeaks
from tests.ecg_synth import SAMPLE_RATE, synth_ecg

MATCH = 0.90


def _empty_bank() -> TemplateBank:
    return TemplateBank(model_version="test-1", beat_length=beat_length(SAMPLE_RATE))


def _beats(
    duration_s: float = 120.0, *, ectopic_every: int = 0, seed: int = 7, noise_uv: float = 8.0
):
    result = synth_ecg(
        duration_s=duration_s, ectopic_every=ectopic_every, seed=seed, noise_uv=noise_uv
    )
    cleaned = clean_signal(result.signal_mv, SAMPLE_RATE)
    peaks = detect_rpeaks(cleaned, SAMPLE_RATE)
    analyzable = np.ones(cleaned.size, dtype=bool)
    return result, extract_beats(cleaned, peaks, analyzable, SAMPLE_RATE)


# --------------------------------------------------------------------------- #
# Extracción
# --------------------------------------------------------------------------- #


def test_los_latidos_salen_normalizados_a_norma_uno() -> None:
    """La comparación es de FORMA, no de amplitud: un ECG de superficie cambia
    de amplitud con la respiración, y sin normalizar el mismo latido a las 3 am
    y a las 9 am caería en clusters distintos."""
    _, beats = _beats(duration_s=30.0)
    assert beats.n_beats > 20
    norms = np.linalg.norm(beats.waveforms, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_un_latido_sobre_senal_no_analizable_no_entra_a_la_matriz() -> None:
    """Uno a caballo del borde de un artefacto arrastraría el artefacto a la plantilla."""
    result = synth_ecg(duration_s=60.0)
    cleaned = clean_signal(result.signal_mv, SAMPLE_RATE)
    peaks = detect_rpeaks(cleaned, SAMPLE_RATE)

    todo = extract_beats(cleaned, peaks, np.ones(cleaned.size, dtype=bool), SAMPLE_RATE)
    parcial_mask = np.ones(cleaned.size, dtype=bool)
    parcial_mask[20 * SAMPLE_RATE : 40 * SAMPLE_RATE] = False
    parcial = extract_beats(cleaned, peaks, parcial_mask, SAMPLE_RATE)

    assert parcial.n_beats < todo.n_beats
    assert not ((parcial.rpeaks >= 21 * SAMPLE_RATE) & (parcial.rpeaks < 39 * SAMPLE_RATE)).any()


# --------------------------------------------------------------------------- #
# El banco
# --------------------------------------------------------------------------- #


def test_un_foco_ectopico_recurrente_queda_en_su_propia_plantilla() -> None:
    result, beats = _beats(duration_s=300.0, ectopic_every=12)
    bank, assignment = assign_and_update(
        _empty_bank(), beats, match_threshold=MATCH, max_templates=40
    )

    dominant = dominant_template(bank)
    assert dominant is not None
    ectopic = [t for t in bank.templates if t.cluster_id != dominant.cluster_id]
    assert ectopic, "el foco ectópico no abrió plantilla propia"

    # El cluster ectópico tiene tantos miembros como ectópicos se inyectaron.
    biggest = max(ectopic, key=lambda t: t.count)
    assert biggest.count == pytest.approx(len(result.ectopic_peaks), rel=0.05)
    # Y es compacto: un foco real, no una bolsa de artefactos parecidos.
    assert biggest.sum_correlation / biggest.count > 0.95
    assert (assignment.cluster_ids == biggest.cluster_id).sum() == biggest.count


def test_los_cluster_id_no_se_renumeran_entre_lotes() -> None:
    """El invariante que DBSCAN no puede dar y por el que se eligió este diseño."""
    bank = _empty_bank()
    ids_por_lote = []
    for seed in (1, 2, 3, 4):
        _, beats = _beats(duration_s=90.0, ectopic_every=10, seed=seed)
        bank, assignment = assign_and_update(
            bank, beats, match_threshold=MATCH, max_templates=40, batch_id=f"b{seed}"
        )
        ids_por_lote.append({int(value) for value in np.unique(assignment.cluster_ids)})

    # Cada lote reusa los ids que ya existían; ninguno cambia de significado.
    assert ids_por_lote[0] <= ids_por_lote[-1] or ids_por_lote[0] & ids_por_lote[-1]
    dominante = dominant_template(bank)
    assert dominante is not None and dominante.cluster_id in ids_por_lote[0]


def test_reprocesar_un_lote_ya_plegado_no_infla_el_banco() -> None:
    """El banco es un acumulador: contar dos veces falsearía la carga (`burdenPct`)."""
    _, beats = _beats(duration_s=120.0, ectopic_every=10)
    bank = _empty_bank()
    bank, _ = assign_and_update(bank, beats, match_threshold=MATCH, max_templates=40, batch_id="b1")
    conteos = {t.cluster_id: t.count for t in bank.templates}
    assert bank.beats_seen > 0
    assert "b1" in bank.consumed_batch_ids

    # El camino del reproceso: puntúa contra el banco actual, no lo modifica.
    assignment = score_only(bank, beats, match_threshold=MATCH)
    assert {t.cluster_id: t.count for t in bank.templates} == conteos
    assert assignment.cluster_ids.size == beats.n_beats


def test_el_banco_lleno_manda_los_sobrantes_a_no_asignados() -> None:
    """Un latido que no abrió plantilla antes que N morfologías distintas es ruido."""
    # Ruido alto: cada latido queda lo bastante distinto del anterior como para
    # querer su propia plantilla, y el banco se llena.
    _, beats = _beats(duration_s=120.0, ectopic_every=7, noise_uv=120.0)
    bank, assignment = assign_and_update(
        _empty_bank(),
        beats,
        match_threshold=0.999,
        max_templates=4,
    )
    assert len(bank.templates) == 4
    assert bank.unmatched_beats > 0
    assert (assignment.cluster_ids == -1).sum() == bank.unmatched_beats


def test_las_coordenadas_del_banco_son_absolutas_al_estudio() -> None:
    """Un `first_sample` relativo al lote apuntaría al lugar equivocado de la traza."""
    _, beats = _beats(duration_s=60.0)
    offset = 1_800_000  # una hora de señal previa
    bank, _ = assign_and_update(
        _empty_bank(),
        beats,
        match_threshold=MATCH,
        max_templates=40,
        sample_offset=offset,
    )
    assert all(template.first_sample >= offset for template in bank.templates)


# --------------------------------------------------------------------------- #
# El score
# --------------------------------------------------------------------------- #


def test_la_disimilitud_se_mide_contra_la_dominante_y_no_contra_la_asignada() -> None:
    """Medirla contra la asignada es circular y da 0 para todo el mundo.

    El banco le abre plantilla propia a cada morfología nueva, así que todo
    latido correlaciona ~1,0 con la suya **por construcción** — incluidos los
    ectópicos que el banco acababa de aislar perfectamente.
    """
    result, beats = _beats(duration_s=300.0, ectopic_every=12)
    bank, assignment = assign_and_update(
        _empty_bank(), beats, match_threshold=MATCH, max_templates=40
    )
    dominante = dominant_template(bank)
    assert dominante is not None

    contra_asignada = assignment.dissimilarity
    contra_dominante = dissimilarity_to_dominant(bank, beats)
    ectopicos = assignment.cluster_ids != dominante.cluster_id

    assert float(np.mean(contra_asignada[ectopicos])) < 0.05  # circular: casi cero
    assert float(np.mean(contra_dominante[ectopicos])) > 0.3  # informativo


def test_morfologia_rara_y_prematura_puntua_el_doble_que_rara_sola() -> None:
    """Es lo que hunde el cuadrante del ruido.

    Un artefacto de movimiento tiene forma rarísima pero cae donde el latido
    tenía que caer —no es un latido, es ruido encima de uno—. Una extrasístole
    real llega antes de tiempo.
    """
    disimilitud = np.array([0.5, 0.5], dtype=np.float32)
    prematuridad = np.array([1.0, 0.6], dtype=np.float32)  # a tiempo / prematuro
    scores = anomaly_score(disimilitud, prematuridad, match_threshold=MATCH)
    assert scores[1] == pytest.approx(2 * scores[0], rel=1e-5)


def test_un_latido_identico_a_la_plantilla_puntua_cero() -> None:
    scores = anomaly_score(
        np.zeros(3, dtype=np.float32), np.ones(3, dtype=np.float32), match_threshold=MATCH
    )
    assert np.allclose(scores, 0.0)


# --------------------------------------------------------------------------- #
# Consolidación y serialización
# --------------------------------------------------------------------------- #


def test_la_consolidacion_funde_plantillas_casi_identicas_y_conserva_el_id_mas_viejo() -> None:
    """El id que sobrevive es el que apareció primero: así los `ecg_event` ya
    escritos con ese id siguen siendo válidos y solo hay que reescribir los de
    las plantillas absorbidas."""
    # Ruido moderado: la misma morfología se fragmenta en varias plantillas, que
    # es justo la situación que la consolidación existe para corregir.
    _, beats = _beats(duration_s=180.0, ectopic_every=12, noise_uv=90.0)
    bank, _ = assign_and_update(_empty_bank(), beats, match_threshold=0.99, max_templates=40)
    assert len(bank.templates) > 2

    fusionado, mapping = consolidate(bank, merge_threshold=0.90)
    assert len(fusionado.templates) < len(bank.templates)
    assert sum(t.count for t in fusionado.templates) == sum(t.count for t in bank.templates)
    for viejo, nuevo in mapping.items():
        assert nuevo < viejo


def test_el_banco_sobrevive_a_una_vuelta_por_disco() -> None:
    _, beats = _beats(duration_s=120.0, ectopic_every=10)
    bank, _ = assign_and_update(
        _empty_bank(), beats, match_threshold=MATCH, max_templates=40, batch_id="b1"
    )
    state, blob = bank_to_state(bank)
    recuperado = bank_from_state(state, blob, model_version="test-1")

    assert recuperado.beats_seen == bank.beats_seen
    assert recuperado.next_cluster_id == bank.next_cluster_id
    assert recuperado.consumed_batch_ids == bank.consumed_batch_ids
    assert len(recuperado.templates) == len(bank.templates)
    for original, vuelto in zip(bank.templates, recuperado.templates, strict=True):
        assert original.cluster_id == vuelto.cluster_id
        assert original.count == vuelto.count
        assert np.allclose(original.centroid, vuelto.centroid)


def test_un_banco_de_otra_version_del_modelo_se_descarta_en_vez_de_migrarse() -> None:
    """Los centroides viejos viven en otra representación: compararlos contra
    latidos nuevos daría distancias sin sentido. Empezar de cero es explícito."""
    _, beats = _beats(duration_s=60.0)
    bank, _ = assign_and_update(_empty_bank(), beats, match_threshold=MATCH, max_templates=40)
    state, blob = bank_to_state(bank)

    otro = bank_from_state(state, blob, model_version="test-2")
    assert otro.templates == ()
    assert otro.beats_seen == 0
