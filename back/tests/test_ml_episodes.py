"""Agregación en episodios — la capa que decide si el motor es usable.

Un detector con 99 % de especificidad **por latido** produce ~1.000 falsos
positivos por día sobre 100.000 latidos. Estos tests fijan las reglas que
convierten eso en algo que un médico puede leer en media hora.
"""

import numpy as np
import pytest

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml.contracts import EpisodeBudget, Finding
from app.ml.episodes import (
    MAX_EPISODE_GAP_SECONDS,
    apply_refractory,
    enforce_budget,
    group_beats,
    open_edges,
)

FS = 500
BUDGET = EpisodeBudget(
    refractory_seconds=10.0,
    gap_beats=3,
    min_beats=2,
    max_per_study=200,
    max_per_kind=50,
    score_floor=0.0,
)


def _group(rpeaks, positive, scores, clusters, recurrent=frozenset(), budget=BUDGET):
    return group_beats(
        np.array(rpeaks, dtype=np.int64),
        np.array(positive, dtype=bool),
        np.array(scores, dtype=np.float32),
        np.array(clusters, dtype=np.int64),
        recurrent,
        sample_rate=FS,
        budget=budget,
    )


# --------------------------------------------------------------------------- #
# Contigüidad
# --------------------------------------------------------------------------- #


def test_latidos_anomalos_seguidos_son_un_episodio_y_no_veinte_hallazgos() -> None:
    rpeaks = [i * 500 for i in range(10)]
    positive = [False, True, True, True, False, False, False, False, False, False]
    findings = _group(rpeaks, positive, [0.9] * 10, [1] * 10)
    assert len(findings) == 1
    assert findings[0].beat_count == 3


def test_el_hueco_se_mide_en_latidos_para_no_partir_un_bigeminismo() -> None:
    """Un bigeminismo alterna normal/ectópico. Con un umbral en segundos se
    partiría en veinte hallazgos a 100 lpm y en uno solo a 50 — el mismo
    fenómeno clínico contado de dos maneras según la frecuencia del paciente."""
    rpeaks = [i * 300 for i in range(12)]
    positive = [i % 2 == 1 for i in range(12)]
    findings = _group(rpeaks, positive, [0.9] * 12, [1] * 12)
    assert len(findings) == 1
    assert findings[0].beat_count == 6


def test_un_hueco_temporal_grande_parte_el_episodio_aunque_los_latidos_sean_vecinos() -> None:
    """El gate saca latidos de la matriz: dos ectópicos separados por 12 s de
    electrodo despegado quedan ADYACENTES en el índice, y sin tope temporal
    producirían una banda de 12 s con dos latidos adentro — sobre señal que ni
    siquiera se evaluó."""
    gap = int((MAX_EPISODE_GAP_SECONDS + 2.0) * FS)
    rpeaks = [1_000, 1_000 + gap]
    # Foco recurrente, para que los dos singletons resultantes sobrevivan al
    # mínimo de latidos y se pueda contar en cuántos episodios quedaron.
    findings = _group(rpeaks, [True, True], [0.9, 0.9], [1, 1], recurrent=frozenset({1}))
    assert len(findings) == 2
    assert all(finding.length_samples < MAX_EPISODE_GAP_SECONDS * FS for finding in findings)


def test_dos_morfologias_distintas_no_se_mezclan_en_un_episodio() -> None:
    rpeaks = [i * 500 for i in range(4)]
    findings = _group(rpeaks, [True] * 4, [0.9] * 4, [1, 1, 2, 2])
    assert len(findings) == 2
    assert {finding.cluster_id for finding in findings} == {1, 2}


# --------------------------------------------------------------------------- #
# El discriminador de §4.4
# --------------------------------------------------------------------------- #


def test_un_latido_suelto_sin_foco_conocido_es_ruido_y_se_descarta() -> None:
    findings = _group([1_000], [True], [0.9], [7], recurrent=frozenset())
    assert findings == []


def test_un_latido_suelto_de_un_foco_recurrente_si_es_real() -> None:
    """La excepción que hace funcionar el método: un ectópico aislado de una
    morfología que aparece 400 veces en el estudio es un hallazgo; uno que no se
    parece a nada es un artefacto."""
    findings = _group([1_000], [True], [0.9], [7], recurrent=frozenset({7}))
    assert len(findings) == 1
    assert findings[0].metadata["recurrent"] == 1


# --------------------------------------------------------------------------- #
# Refractariedad
# --------------------------------------------------------------------------- #


def _finding(start: int, *, kind: str = "pause", cluster: int | None = None, beats: int = 1):
    return Finding(
        kind=kind,
        event_type=ECGEventType.PAUSE,
        severity=ECGEventSeverity.HIGH,
        start_sample=start,
        length_samples=500,
        dedupe_key=f"{kind}:{start}",
        score=0.5,
        cluster_id=cluster,
        beat_count=beats,
    )


def test_dos_episodios_cercanos_se_funden_sumando_ocurrencias() -> None:
    """Se funden, NO se descartan: el conteo de ocurrencias es justo el número
    que el médico usa para decidir si vale la pena mirar."""
    merged = apply_refractory(
        [_finding(0, beats=3), _finding(2_000, beats=2)],
        sample_rate=FS,
        refractory_seconds=10.0,
    )
    assert len(merged) == 1
    assert merged[0].beat_count == 5


def test_dos_episodios_lejanos_siguen_siendo_dos() -> None:
    merged = apply_refractory(
        [_finding(0), _finding(30 * FS)], sample_rate=FS, refractory_seconds=10.0
    )
    assert len(merged) == 2


# --------------------------------------------------------------------------- #
# Presupuesto de revisión
# --------------------------------------------------------------------------- #


def _anomaly(start: int, score: float) -> Finding:
    return Finding(
        kind="morphology_anomaly",
        event_type=ECGEventType.ANOMALY,
        severity=ECGEventSeverity.LOW,
        start_sample=start,
        length_samples=250,
        dedupe_key=f"morphology_anomaly:{start}",
        score=score,
    )


def test_el_tope_por_tipo_recorta_y_conserva_los_mas_atipicos() -> None:
    budget = EpisodeBudget(10.0, 3, 2, 200, 5, 0.0)
    findings = [_anomaly(i * 1_000, score=i / 100) for i in range(20)]
    admitted, _ = enforce_budget(findings, budget=budget)
    assert len(admitted) == 5
    assert {round(f.score or 0, 4) for f in admitted} == {0.19, 0.18, 0.17, 0.16, 0.15}


def test_una_pausa_critica_no_pierde_el_lugar_contra_doscientas_morfologias() -> None:
    """El orden es por severidad primero. Una pausa de 4 s con `score=None` no
    puede quedar afuera porque doscientas morfologías tengan score más alto."""
    budget = EpisodeBudget(10.0, 3, 2, 200, 2, 0.0)
    pausa = _finding(999, kind="pause")
    admitted, _ = enforce_budget(
        [_anomaly(i * 1_000, 0.9) for i in range(50)] + [pausa], budget=budget
    )
    assert pausa.dedupe_key in {finding.dedupe_key for finding in admitted}


def test_el_piso_de_score_sube_cuando_el_estudio_llena_su_presupuesto() -> None:
    """El tope es por ESTUDIO pero los lotes llegan de a uno: no se puede
    "quedarse con el top-N" sin ver el futuro. El piso móvil hace que el proceso
    converja al top-N real con escrituras constantes por lote."""
    budget = EpisodeBudget(10.0, 3, 2, 10, 50, 0.0)
    findings = [_anomaly(i * 1_000, score=i / 100) for i in range(20)]
    admitted, floor = enforce_budget(findings, budget=budget, existing_anomalies=8)
    assert len(admitted) == 2  # ya había 8 de 10
    assert floor > 0.0

    # El lote siguiente descarta de entrada todo lo que no supera el piso.
    siguiente = EpisodeBudget(10.0, 3, 2, 10, 50, floor)
    admitted_2, _ = enforce_budget(
        [_anomaly(50_000, score=floor / 2)], budget=siguiente, existing_anomalies=10
    )
    assert admitted_2 == []


def test_los_hallazgos_de_estudio_no_compiten_contra_el_tope_de_anomalias() -> None:
    """El encabezado por morfología es un título de grupo, no un episodio."""
    budget = EpisodeBudget(10.0, 3, 2, 1, 50, 0.0)
    header = Finding(
        kind="recurrent_morphology",
        event_type=ECGEventType.ANOMALY,
        severity=ECGEventSeverity.MEDIUM,
        start_sample=0,
        length_samples=1_000_000,
        dedupe_key="cluster:1",
        score=0.8,
        scope="study",
    )
    admitted, _ = enforce_budget([header], budget=budget, existing_anomalies=99)
    assert admitted == [header]


def test_sin_hallazgos_no_pasa_nada() -> None:
    assert enforce_budget([], budget=BUDGET) == ([], 0.0)
    assert apply_refractory([], sample_rate=FS, refractory_seconds=10.0) == []
    assert _group([], [], [], []) == []


@pytest.mark.parametrize(
    "score,expected", [(0.5, ECGEventSeverity.LOW), (0.8, ECGEventSeverity.MEDIUM)]
)
def test_la_severidad_de_morfologia_nunca_llega_a_high(
    score: float, expected: ECGEventSeverity
) -> None:
    """El alcance del sistema es explícitamente no diagnóstico: "esto no se
    parece a tus otros latidos" no justifica despertar a nadie a las 3 am."""
    findings = _group([0, 500], [True, True], [score, score], [1, 1], recurrent=frozenset({1}))
    assert findings[0].severity is expected


def test_un_lado_abierto_solo_queda_si_todo_lo_que_llega_ahi_es_abierto() -> None:
    """`openStart`/`openEnd` (`quiet_gap`) de un evento fundido: el bloque que
    vio la asistolia en curso la informó abierta a la derecha y el que leyó el
    R que la cierra, abierta a la izquierda. Fundidas, tienen los dos R: ningún
    lado queda abierto, y cada cota es la del R de su lado."""
    abierta_a_la_derecha = {"openEnd": True, "firstBeatRatio": 1.01}
    abierta_a_la_izquierda = {"openStart": True, "lastBeatRatio": 0.98}
    fundida = open_edges(
        {**abierta_a_la_izquierda, **abierta_a_la_derecha},
        [(100, 500, abierta_a_la_derecha), (300, 800, abierta_a_la_izquierda)],
    )
    assert "openStart" not in fundida and "openEnd" not in fundida
    assert fundida["firstBeatRatio"] == 1.01
    assert fundida["lastBeatRatio"] == 0.98

    # Un duplicado abierto desde el principio de su lectura, adentro de la pausa
    # entera: la pausa sigue teniendo el R que la abre.
    entera = {"firstBeatRatio": 1.0, "lastBeatRatio": 1.0}
    duplicado = {"openStart": True, "lastBeatRatio": 1.0}
    fundida = open_edges({**duplicado, **entera}, [(100, 800, entera), (300, 800, duplicado)])
    assert "openStart" not in fundida

    # Si las dos partes que llegan al final son abiertas, el final sigue abierto.
    otra = {"openEnd": True, "firstBeatRatio": 0.9}
    fundida = open_edges(
        {**otra, **abierta_a_la_derecha},
        [(100, 500, abierta_a_la_derecha), (200, 500, otra)],
    )
    assert fundida["openEnd"] is True
    assert "lastBeatRatio" not in fundida
