"""De latidos anómalos a hallazgos que un médico puede leer.

Es la capa que decide si el motor sirve o no. Un detector con 99 % de
especificidad **por latido** produce ~1.000 falsos positivos por día sobre
100.000 latidos: técnicamente excelente, clínicamente inservible. Nadie revisa
mil marcas.

Cuatro reglas, en orden:

1. **Contigüidad.** Latidos anómalos cercanos y de la misma morfología son *un*
   episodio, no veinte hallazgos.
2. **Mínimo, con la excepción que hace funcionar el método.** Un latido suelto se
   descarta… salvo que pertenezca a un cluster recurrente. Un ectópico aislado de
   un foco conocido es real; uno que no se parece a nada es un artefacto.
3. **Refractariedad.** Dos episodios del mismo foco separados por segundos se
   funden, sumando ocurrencias. Fundir y no descartar: el número de ocurrencias
   es justo lo que el médico va a mirar.
4. **Tope duro por estudio**, con piso móvil. Ver `enforce_budget`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml.contracts import EpisodeBudget, Finding, Floats, Indices, Mask
from app.ml.morphology import BEAT_POST_MS, BEAT_PRE_MS

#: Score a partir del cual el hallazgo sube de severidad. No hay `HIGH` para
#: morfología: el alcance del sistema es explícitamente no diagnóstico, y "esto
#: no se parece a tus otros latidos" no justifica despertar a nadie.
MEDIUM_SCORE = 0.7

ANOMALY_KIND = "morphology_anomaly"
RECURRENT_KIND = "recurrent_morphology"

#: Tope temporal del hueco dentro de un episodio, además del tope en latidos.
#:
#: Hace falta porque el gate de calidad **saca latidos de la matriz**: los que
#: caen en un tramo no analizable no llegan hasta acá. Dos ectópicos separados
#: por 12 s de electrodo despegado quedan adyacentes en el índice de la matriz y
#: el hueco en latidos los da por contiguos — produciendo una banda de 12 s con
#: dos latidos adentro, sobre señal que además ni siquiera se evaluó. Cinco
#: segundos entran tres latidos incluso a 40 lpm.
MAX_EPISODE_GAP_SECONDS = 5.0


def group_beats(
    rpeaks: Indices,
    positive: Mask,
    scores: Floats,
    cluster_ids: Indices,
    recurrent: frozenset[int],
    *,
    sample_rate: int,
    budget: EpisodeBudget,
    owned: Mask | None = None,
) -> list[Finding]:
    """Agrupa latidos positivos en episodios. Coordenadas **relativas al lote**.

    `owned` marca los latidos de la parte nueva de un bloque con contexto: los
    del contexto entran en el agrupamiento —un episodio no se corta en el borde
    del bloque— pero un grupo que no tiene **ningún** latido nuevo se descarta,
    porque ya lo informó el bloque anterior. Sin `owned`, todos son nuevos.
    """
    if rpeaks.size == 0 or not positive.any():
        return []

    pre = int(round(BEAT_PRE_MS * sample_rate / 1000))
    post = int(round(BEAT_POST_MS * sample_rate / 1000))
    max_gap_samples = int(MAX_EPISODE_GAP_SECONDS * sample_rate)
    indices = np.flatnonzero(positive)

    groups: list[list[int]] = []
    for index in indices:
        current = int(index)
        previous = groups[-1][-1] if groups else None
        if (
            previous is not None
            # El hueco se mide en LATIDOS y no en segundos: un bigeminismo alterna
            # normal/ectópico, y un umbral temporal lo partiría en veinte
            # hallazgos a 100 lpm y en uno solo a 50. El tope temporal de abajo
            # es solo una salvaguarda contra los latidos que el gate descartó.
            and current - previous <= budget.gap_beats
            and int(rpeaks[current]) - int(rpeaks[previous]) <= max_gap_samples
            and int(cluster_ids[previous]) == int(cluster_ids[current])
        ):
            groups[-1].append(current)
        else:
            groups.append([current])

    findings: list[Finding] = []
    for group in groups:
        if owned is not None and not owned[group].any():
            continue
        cluster_id = int(cluster_ids[group[0]])
        is_recurrent = cluster_id in recurrent
        if len(group) < budget.min_beats and not is_recurrent:
            continue
        start = max(int(rpeaks[group[0]]) - pre, 0)
        end = int(rpeaks[group[-1]]) + post
        score = float(np.max(scores[group]))
        findings.append(
            Finding(
                kind=ANOMALY_KIND,
                event_type=ECGEventType.ANOMALY,
                severity=ECGEventSeverity.MEDIUM if score >= MEDIUM_SCORE else ECGEventSeverity.LOW,
                start_sample=start,
                length_samples=max(end - start, 1),
                dedupe_key=f"{ANOMALY_KIND}:{start}",
                score=round(score, 6),
                cluster_id=cluster_id if cluster_id >= 0 else None,
                beat_count=len(group),
                metadata={
                    "meanScore": round(float(np.mean(scores[group])), 6),
                    "recurrent": int(is_recurrent),
                },
                beat_samples=tuple(int(rpeaks[index]) for index in group),
            )
        )
    return findings


def apply_refractory(
    findings: list[Finding], *, sample_rate: int, refractory_seconds: float
) -> list[Finding]:
    """Funde hallazgos del mismo tipo y foco separados por menos de la ventana.

    Se **funden**, no se descartan: descartar el segundo episodio de un foco
    mentiría en el conteo de ocurrencias, que es el número que el médico usa para
    decidir si vale la pena mirar.
    """
    if not findings:
        return []
    window = int(refractory_seconds * sample_rate)
    ordered = sorted(
        findings, key=lambda item: (item.kind, item.cluster_id or -1, item.start_sample)
    )

    merged: list[Finding] = []
    for finding in ordered:
        previous = merged[-1] if merged else None
        same_group = (
            previous is not None
            and previous.kind == finding.kind
            and previous.cluster_id == finding.cluster_id
        )
        if previous is None or not same_group:
            merged.append(finding)
            continue
        previous_end = previous.start_sample + previous.length_samples
        if finding.start_sample - previous_end > window:
            merged.append(finding)
            continue
        end = max(previous_end, finding.start_sample + finding.length_samples)
        length = end - previous.start_sample
        # Los latidos son la unión: dos pausas seguidas comparten el R del medio,
        # y sumar los conteos lo contaba dos veces.
        beat_samples = tuple(sorted(set(previous.beat_samples) | set(finding.beat_samples)))
        merged[-1] = Finding(
            kind=previous.kind,
            event_type=previous.event_type,
            severity=max(
                previous.severity,
                finding.severity,
                key=lambda value: _SEVERITY_RANK[value],
            ),
            start_sample=previous.start_sample,
            length_samples=length,
            dedupe_key=previous.dedupe_key,
            score=max(previous.score or 0.0, finding.score or 0.0),
            scope=previous.scope,
            cluster_id=previous.cluster_id,
            beat_count=len(beat_samples)
            if beat_samples
            else (previous.beat_count or 0) + (finding.beat_count or 0),
            alert_message=previous.alert_message or finding.alert_message,
            metadata=open_edges(
                _merge_metadata(previous.metadata, finding.metadata, length, sample_rate),
                [
                    (previous.start_sample, previous_end, previous.metadata),
                    (
                        finding.start_sample,
                        finding.start_sample + finding.length_samples,
                        finding.metadata,
                    ),
                ],
            ),
            beat_samples=beat_samples,
        )
    merged.sort(key=lambda item: item.start_sample)
    return merged


#: Metadata que resume un extremo del episodio: al fundir dos, manda el más
#: extremo de los dos y no el del primero.
_METADATA_MAX = ("peakBpm", "pauseSeconds")
_METADATA_MIN = ("minBpm",)


def _merge_metadata(
    previous: dict[str, float | int | str],
    finding: dict[str, float | int | str],
    length_samples: int,
    sample_rate: int,
) -> dict[str, float | int | str]:
    """La metadata del hallazgo fundido, recalculada donde depende del largo.

    Antes mandaba la del primero entera, y una taquicardia fundida con la que la
    seguía declaraba la duración de la primera sola. Ahora `durationSeconds` sale
    del largo fundido, `peakBpm` y `pauseSeconds` toman el máximo de los dos y
    `minBpm` el mínimo. `medianBpm` **se saca**: una mediana no se puede
    recomponer a partir de dos, y quedarse con la del primero describía solo el
    arranque — una taquicardia de veinte minutos que pasa de 110 a 150 lpm
    declaraba una mediana de 110. El resto sigue siendo el del primero.
    """
    metadata = {**finding, **previous}
    metadata.pop("medianBpm", None)
    for key in _METADATA_MAX:
        values = [float(item[key]) for item in (previous, finding) if key in item]
        if values:
            metadata[key] = max(values)
    for key in _METADATA_MIN:
        values = [float(item[key]) for item in (previous, finding) if key in item]
        if values:
            metadata[key] = min(values)
    if "durationSeconds" in metadata:
        metadata["durationSeconds"] = round(length_samples / sample_rate, 2)
    return metadata


def open_edges(
    metadata: dict[str, Any], parts: Sequence[tuple[int, int, Mapping[str, Any]]]
) -> dict[str, Any]:
    """`openStart` y `openEnd` del hallazgo fundido (`quiet_gap`), de las partes
    `(inicio, fin, metadata)` que lo forman.

    `openStart` solo si todo lo que empieza donde empieza la unión es un tramo
    abierto a la izquierda, y `openEnd` igual con el final. Si alguna parte
    empieza en un R, la pausa tiene R que la abre aunque otro bloque la haya
    visto sin él (y su `firstBeatRatio` es el de ese R); lo mismo con el que la
    cierra.
    """
    first = min(start for start, _, _ in parts)
    last = max(end for _, end, _ in parts)
    for key, ratio, at_edge in (
        ("openStart", "firstBeatRatio", [item for start, _, item in parts if start == first]),
        ("openEnd", "lastBeatRatio", [item for _, end, item in parts if end == last]),
    ):
        if at_edge and all(item.get(key) for item in at_edge):
            metadata[key] = True
            metadata.pop(ratio, None)
        else:
            metadata.pop(key, None)
            known = [item[ratio] for item in at_edge if ratio in item]
            if known:
                metadata[ratio] = known[0]
    return metadata


_SEVERITY_RANK = {
    ECGEventSeverity.LOW: 0,
    ECGEventSeverity.MEDIUM: 1,
    ECGEventSeverity.HIGH: 2,
    ECGEventSeverity.CRITICAL: 3,
}


def enforce_budget(
    findings: list[Finding], *, budget: EpisodeBudget, existing_anomalies: int = 0
) -> tuple[list[Finding], float]:
    """Recorta al presupuesto de revisión y devuelve el piso de score resultante.

    El tope es *por estudio* pero los lotes llegan de a uno, así que no se puede
    "quedarse con el top-N" sin ver el futuro. Se implementa como **admisión con
    piso móvil**: cada lote descarta lo que no supera el piso actual y, cuando el
    estudio queda por encima del tope, el piso sube al score del último admitido.
    Como el piso solo sube, el proceso converge al top-N real del estudio con un
    número constante de escrituras por lote.

    Los hallazgos de ritmo y de calidad **no** compiten contra el tope de
    anomalías: su cardinalidad natural es de decenas y ahogarlos con morfologías
    escondería justo lo que más importa.
    """
    if not findings:
        return [], budget.score_floor

    admitted: list[Finding] = []
    per_kind: dict[str, int] = {}
    # Severidad primero: una pausa crítica no puede perder el lugar contra
    # doscientas morfologías atípicas por tener el score más bajo.
    ordered = sorted(
        findings,
        key=lambda item: (-_SEVERITY_RANK[item.severity], -(item.score or 0.0), item.start_sample),
    )
    anomalies = existing_anomalies
    floor = budget.score_floor

    for finding in ordered:
        is_anomaly = finding.event_type is ECGEventType.ANOMALY and finding.scope == "batch"
        if is_anomaly and (finding.score or 0.0) < budget.score_floor:
            continue
        used = per_kind.get(finding.kind, 0)
        if used >= budget.max_per_kind:
            continue
        if is_anomaly and anomalies >= budget.max_per_study:
            # El presupuesto se agotó: el piso sube al score del último que entró
            # para que los lotes siguientes ni siquiera escriban los peores.
            floor = max(floor, finding.score or 0.0)
            continue
        per_kind[finding.kind] = used + 1
        if is_anomaly:
            anomalies += 1
        admitted.append(finding)

    admitted.sort(key=lambda item: (item.start_sample, item.kind))
    return admitted, floor
