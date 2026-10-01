"""Etapa 2 — la plantilla del paciente y el banco de morfologías.

La idea que hace que esto funcione sin un solo latido etiquetado: **cada
paciente es su propio control**. No hace falta saber cómo es un latido normal en
general; alcanza con saber cómo son los de *este* paciente, y eso se aprende de
sus propias 24 h. Un latido que no se parece a los suyos es un hallazgo aunque
nadie sepa ponerle nombre.

## Por qué un banco de plantillas y no PCA + DBSCAN

El documento de estrategia proponía clusterizar el estudio con PCA(10) + DBSCAN.
Medido, eso no llega: a 24 h son ~100.000 latidos y DBSCAN muere por OOM (escala
~O(n²) porque con `eps` grande casi todos los latidos son vecinos entre sí). A
8 h ya tarda 15 s.

Pero el costo no es lo que decide. Lo que decide es que **DBSCAN no da etiquetas
estables**: los `label_` de una corrida no tienen relación con los de la
anterior. Como el `clusterId` viaja dentro de `event_metadata` de cada
`ecg_event` ya escrito, reclusterizar en la hora 4 haría mentir a todas las
filas de la hora 3.

Un banco de plantillas resuelve las dos cosas a la vez:

- Los `cluster_id` se asignan **una vez y nunca se renumeran**.
- El estado es O(#plantillas) y no O(#latidos): 40 centroides de 250 muestras son
  40 KB por estudio, contra 100 MB de matriz de latidos.
- Cada latido se compara con ≤ 40 centroides mediante **un solo matmul** por
  lote: milisegundos contra decenas de segundos.
- Es un acumulador monótono: un foco ectópico de 17 latidos por hora llega a 412
  miembros al final del estudio sin releer un byte de historia.

Es, además, lo que hace el software de Holter comercial: "beat classes" por
correlación. `scikit-learn` aparece una sola vez en todo el motor, para fundir
plantillas que derivaron hacia la misma forma cuando el estudio cierra.

## La representación

Ventana de ±250 ms alrededor del R, menos su mediana, normalizada a norma 1. La
distancia es `1 − producto punto`, es decir la distancia de correlación. **No hay
PCA**: una base aprendida rotaría entre lotes y obligaría a reproyectar todos los
centroides guardados. Sin base aprendida no rota nada.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from app.ml.contracts import Floats, Indices, Mask, Signal

#: Ventana del latido, en milisegundos alrededor del pico R. 250 ms hacia atrás
#: entran la P y el arranque del QRS; 250 ms hacia adelante, el final del QRS y
#: el ST. Es lo que necesita distinguir un ectópico ventricular (QRS ancho, T
#: opuesta) de un latido normal.
BEAT_PRE_MS = 250
BEAT_POST_MS = 250

#: Formato del estado serializado. Sube cuando cambia la representación del
#: latido: un banco viejo deja de ser comparable y hay que empezar uno nuevo.
STATE_SCHEMA_VERSION = 1


def beat_length(sample_rate: int) -> int:
    return int(round((BEAT_PRE_MS + BEAT_POST_MS) * sample_rate / 1000))


@dataclass(frozen=True, slots=True)
class BeatMatrix:
    """Latidos extraíbles del lote, alineados y normalizados.

    `beat_index` es la posición del latido dentro del array de R-peaks original.
    Hace falta porque no todos los latidos entran: los de los bordes del lote no
    tienen ventana completa y los que caen en señal no analizable se descartan.
    Sin ese índice no se podría volver a alinear el score con la serie R-R.
    """

    beat_index: Indices
    rpeaks: Indices
    #: `(n_beats, beat_length)`, cada fila con norma 1.
    waveforms: Floats

    @property
    def n_beats(self) -> int:
        return int(self.rpeaks.size)


@dataclass(frozen=True, slots=True)
class Template:
    cluster_id: int
    centroid: Floats
    count: int
    #: Suma de las correlaciones de sus miembros. Dividida por `count` da la
    #: compacidad del cluster, que es lo que separa un foco real (muy compacta)
    #: de una bolsa de artefactos que casualmente se parecieron.
    sum_correlation: float
    first_sample: int
    last_sample: int


@dataclass(frozen=True, slots=True)
class TemplateBank:
    model_version: str
    beat_length: int
    templates: tuple[Template, ...] = ()
    beats_seen: int = 0
    #: Latidos que no entraron en ninguna plantilla porque el banco estaba lleno.
    #: Son candidatos a ruido: si fueran un foco recurrente habrían abierto una
    #: plantilla antes de llegar al tope.
    unmatched_beats: int = 0
    next_cluster_id: int = 0
    #: Lotes ya plegados al banco. Reprocesar uno de estos **no** vuelve a sumar
    #: sus latidos: el banco es un acumulador y contarlos dos veces falsearía la
    #: carga (`burdenPct`) que el médico lee.
    consumed_batch_ids: tuple[str, ...] = ()
    score_floor: float = 0.0

    def centroids(self) -> Floats:
        if not self.templates:
            return np.empty((0, self.beat_length), dtype=np.float32)
        return np.stack([template.centroid for template in self.templates])

    def recurrent_ids(self, min_beats: int) -> frozenset[int]:
        """Clusters con miembros suficientes para no ser ruido disperso."""
        return frozenset(t.cluster_id for t in self.templates if t.count >= min_beats)


@dataclass(frozen=True, slots=True)
class BeatAssignment:
    #: Por latido de la `BeatMatrix`. `-1` = no entró en ninguna plantilla.
    cluster_ids: Indices
    #: `1 − correlación` con la plantilla más parecida. 1,0 si no hubo ninguna.
    dissimilarity: Floats


# --------------------------------------------------------------------------- #
# Extracción
# --------------------------------------------------------------------------- #


def extract_beats(
    signal: Signal, rpeaks: Indices, analyzable: Mask, sample_rate: int
) -> BeatMatrix:
    """Matriz de latidos alineados al pico R, centrados y normalizados.

    Restar la mediana y normalizar a norma 1 hace que la comparación sea de
    **forma** y no de amplitud: la amplitud de un ECG de superficie cambia con la
    respiración y con cuánto se movió el chaleco, y sin normalizar el mismo
    latido a las 3 am y a las 9 am caerían en clusters distintos.
    """
    length = beat_length(sample_rate)
    pre = int(round(BEAT_PRE_MS * sample_rate / 1000))
    empty = BeatMatrix(
        beat_index=np.empty(0, dtype=np.int64),
        rpeaks=np.empty(0, dtype=np.int64),
        waveforms=np.empty((0, length), dtype=np.float32),
    )
    if rpeaks.size == 0 or signal.size < length:
        return empty

    starts = rpeaks - pre
    keep = (starts >= 0) & (starts + length <= signal.size)
    if analyzable.size == signal.size:
        # El latido tiene que caer entero en señal analizable. Uno a caballo del
        # borde de un artefacto arrastraría el artefacto a la plantilla.
        cumulative = np.concatenate(([0], np.cumsum(analyzable.astype(np.int64))))
        ends = np.clip(starts + length, 0, signal.size)
        clipped_starts = np.clip(starts, 0, signal.size)
        good = cumulative[ends] - cumulative[clipped_starts]
        keep &= good == length
    if not keep.any():
        return empty

    beat_index = np.flatnonzero(keep).astype(np.int64)
    kept_starts = starts[keep]
    windows = np.lib.stride_tricks.sliding_window_view(signal, length)[kept_starts]
    centered = windows.astype(np.float32) - np.median(windows, axis=1, keepdims=True).astype(
        np.float32
    )
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    # Norma cero = ventana perfectamente plana. No es un latido: se descarta en
    # vez de dividir por cero y producir NaN que envenenarían el centroide.
    usable = (norms[:, 0] > 0) & np.isfinite(norms[:, 0])
    if not usable.any():
        return empty
    waveforms = (centered[usable] / norms[usable]).astype(np.float32)
    return BeatMatrix(
        beat_index=beat_index[usable],
        rpeaks=rpeaks[keep][usable],
        waveforms=waveforms,
    )


# --------------------------------------------------------------------------- #
# Banco de plantillas
# --------------------------------------------------------------------------- #


def _best_match(centroids: Floats, waveforms: Floats) -> tuple[Indices, Floats]:
    """Plantilla más parecida y su correlación, para todos los latidos de una."""
    if centroids.shape[0] == 0 or waveforms.shape[0] == 0:
        return (
            np.full(waveforms.shape[0], -1, dtype=np.int64),
            np.zeros(waveforms.shape[0], dtype=np.float32),
        )
    # Un solo matmul (n × L) @ (L × k). Con n=3.600, L=250 y k=40 son 36 MFLOP:
    # milisegundos con BLAS, contra decenas de segundos de DBSCAN.
    similarity = waveforms @ centroids.T
    best = np.argmax(similarity, axis=1).astype(np.int64)
    return best, similarity[np.arange(similarity.shape[0]), best].astype(np.float32)


def score_only(bank: TemplateBank, beats: BeatMatrix, *, match_threshold: float) -> BeatAssignment:
    """Asigna latidos al banco **sin modificarlo**.

    Es el camino del reprocesamiento: un lote que ya se plegó al banco se vuelve
    a puntuar contra el banco actual, pero sus latidos no se cuentan de nuevo.
    """
    slot, correlation = _best_match(bank.centroids(), beats.waveforms)
    matched = (slot >= 0) & (correlation >= match_threshold)
    ids = np.full(beats.n_beats, -1, dtype=np.int64)
    if bank.templates:
        lookup = np.array([template.cluster_id for template in bank.templates], dtype=np.int64)
        ids[matched] = lookup[slot[matched]]
    dissimilarity = np.where(matched, 1.0 - correlation, 1.0).astype(np.float32)
    return BeatAssignment(cluster_ids=ids, dissimilarity=dissimilarity)


def assign_and_update(
    bank: TemplateBank,
    beats: BeatMatrix,
    *,
    match_threshold: float,
    max_templates: int,
    batch_id: str | None = None,
    sample_offset: int = 0,
) -> tuple[TemplateBank, BeatAssignment]:
    """Pliega un lote al banco: asigna, crea plantillas nuevas y acumula.

    Dos pasadas a propósito. La primera resuelve de un matmul los latidos que
    caen en plantillas ya existentes —el 99 % en un registro normal—; la segunda
    recorre en Python solo los que sobraron, que son los candidatos a morfología
    nueva. Congelar los centroides durante la primera pasada además hace el
    resultado **independiente del orden** de los latidos dentro del lote.
    """
    if beats.n_beats == 0:
        return bank, BeatAssignment(
            cluster_ids=np.empty(0, dtype=np.int64),
            dissimilarity=np.empty(0, dtype=np.float32),
        )

    centroids = list(bank.centroids())
    templates = list(bank.templates)
    next_id = bank.next_cluster_id
    unmatched = 0

    slot, correlation = _best_match(bank.centroids(), beats.waveforms)
    matched = (slot >= 0) & (correlation >= match_threshold)
    slots = np.where(matched, slot, -1).astype(np.int64)
    best_correlation = np.where(matched, correlation, np.float32(-1.0)).astype(np.float32)

    # --- Pasada 2: los que no cayeron en ninguna plantilla existente --------- #
    for index in np.flatnonzero(~matched):
        waveform = beats.waveforms[index]
        if centroids:
            fresh = np.stack(centroids) @ waveform
            candidate = int(np.argmax(fresh))
            if float(fresh[candidate]) >= match_threshold:
                slots[index] = candidate
                best_correlation[index] = float(fresh[candidate])
                continue
        if len(templates) >= max_templates:
            # El banco está lleno. Un latido que llega acá no abrió plantilla
            # antes que 40 morfologías distintas: es ruido, no un foco.
            unmatched += 1
            continue
        sample = int(beats.rpeaks[index]) + sample_offset
        templates.append(
            Template(
                cluster_id=next_id,
                centroid=waveform.copy(),
                count=0,
                sum_correlation=0.0,
                first_sample=sample,
                last_sample=sample,
            )
        )
        centroids.append(waveform.copy())
        slots[index] = len(templates) - 1
        best_correlation[index] = 1.0
        next_id += 1

    # --- Actualización incremental de los centroides ------------------------- #
    for position, template in enumerate(templates):
        members = np.flatnonzero(slots == position)
        if members.size == 0:
            continue
        total = template.count + members.size
        # Media corrida ponderada por el conteo previo, renormalizada. Equivale a
        # promediar todos los miembros vistos hasta ahora sin guardarlos.
        blended = template.centroid * template.count + beats.waveforms[members].sum(axis=0)
        norm = float(np.linalg.norm(blended))
        centroid = (blended / norm).astype(np.float32) if norm > 0 else template.centroid
        # Absolutas al estudio: el banco cruza lotes, así que un `first_sample`
        # relativo al lote apuntaría al lugar equivocado de la traza.
        samples = beats.rpeaks[members] + sample_offset
        templates[position] = Template(
            cluster_id=template.cluster_id,
            centroid=centroid,
            count=total,
            sum_correlation=template.sum_correlation
            + float(np.sum(np.clip(best_correlation[members], 0.0, 1.0))),
            first_sample=min(template.first_sample, int(samples.min())),
            last_sample=max(template.last_sample, int(samples.max())),
        )

    lookup = np.array([template.cluster_id for template in templates], dtype=np.int64)
    cluster_ids = np.where(slots >= 0, lookup[np.clip(slots, 0, None)], -1).astype(np.int64)
    dissimilarity = np.where(slots >= 0, 1.0 - np.clip(best_correlation, 0.0, 1.0), 1.0).astype(
        np.float32
    )

    consumed = bank.consumed_batch_ids
    if batch_id is not None and batch_id not in consumed:
        consumed = (*consumed, batch_id)

    updated = replace(
        bank,
        templates=tuple(templates),
        beats_seen=bank.beats_seen + beats.n_beats,
        unmatched_beats=bank.unmatched_beats + unmatched,
        next_cluster_id=next_id,
        consumed_batch_ids=consumed,
    )
    return updated, BeatAssignment(cluster_ids=cluster_ids, dissimilarity=dissimilarity)


def dominant_template(bank: TemplateBank) -> Template | None:
    """La plantilla del paciente: la que más miembros tiene.

    No hace falta ninguna heurística más elaborada. Un Holter tiene más del 90 %
    de sus latidos en una sola morfología —la normal de ese paciente— y cualquier
    foco ectópico, por activo que sea, queda muy por debajo.
    """
    if not bank.templates:
        return None
    return max(bank.templates, key=lambda template: template.count)


def dissimilarity_to_dominant(bank: TemplateBank, beats: BeatMatrix) -> Floats:
    """`1 − correlación` de cada latido contra la plantilla **dominante**.

    Es la disimilitud que importa y no la que hay contra la plantilla asignada.
    Medirla contra la asignada es circular: el banco le abre plantilla propia a
    cada morfología nueva, así que **todo latido correlaciona ~1,0 con la suya**
    por construcción y el score de anomalía daría 0 para todo el mundo —
    incluidos los nueve ectópicos que el banco acababa de aislar perfectamente en
    su propio cluster.

    Contra la dominante la pregunta vuelve a ser la del método: *¿cuánto se
    parece este latido a los normales de este paciente?*
    """
    dominant = dominant_template(bank)
    if dominant is None or beats.n_beats == 0:
        return np.ones(beats.n_beats, dtype=np.float32)
    correlation = beats.waveforms @ dominant.centroid
    return np.asarray(np.clip(1.0 - correlation, 0.0, 2.0), dtype=np.float32)


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #


#: Disimilitud a la que el score morfológico satura en 1. El doble del margen que
#: define "no matchea": un latido que quedó al doble de distancia del umbral es
#: tan distinto como el score puede expresar.
def _dissimilarity_reference(match_threshold: float) -> float:
    return max(2.0 * (1.0 - match_threshold), 1e-6)


#: Cuánto tiene que adelantarse un latido para que cuente como plenamente
#: prematuro. 0,4 = llegó al 60 % del R-R esperado, que es el acoplamiento típico
#: de una extrasístole.
PREMATURITY_SPAN = 0.4


def anomaly_score(
    dissimilarity: Floats, prematurity_ratio: Floats, *, match_threshold: float
) -> Floats:
    """Combina los dos ejes del plano de decisión en un escalar de [0, 1].

    Morfología rara **y** prematura puntúa el doble que morfología rara sola. Es
    lo que hunde el cuadrante del ruido: un artefacto de movimiento tiene forma
    rarísima pero cae donde el latido tenía que caer, porque no es un latido — es
    ruido encima de uno. Una extrasístole real llega antes de tiempo.
    """
    if dissimilarity.size == 0:
        return np.empty(0, dtype=np.float32)
    shape = np.clip(dissimilarity / _dissimilarity_reference(match_threshold), 0.0, 1.0)
    earliness = np.clip((1.0 - prematurity_ratio) / PREMATURITY_SPAN, 0.0, 1.0)
    return np.asarray(shape * (0.5 + 0.5 * earliness), dtype=np.float32)


# --------------------------------------------------------------------------- #
# Consolidación al cerrar el estudio
# --------------------------------------------------------------------------- #


def consolidate(
    bank: TemplateBank, *, merge_threshold: float
) -> tuple[TemplateBank, dict[int, int]]:
    """Funde plantillas que derivaron hacia la misma forma. Devuelve `viejo → nuevo`.

    Corre **una vez, al cerrar el estudio**, y sobre ≤ 40 centroides — no sobre
    100.000 latidos. Existe porque el banco es *greedy*: si en la hora 2 aparece
    una forma intermedia entre dos plantillas, el banco puede haber abierto dos
    donde había una sola morfología. Al final se ve completo y se corrige.

    Es el único uso de `scikit-learn` en todo el motor.
    """
    if len(bank.templates) < 2:
        return bank, {}
    from sklearn.cluster import AgglomerativeClustering

    model = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=1.0 - merge_threshold,
    )
    labels = np.asarray(model.fit_predict(bank.centroids().astype(np.float64)), dtype=np.int64)

    merged: list[Template] = []
    mapping: dict[int, int] = {}
    for label in np.unique(labels):
        group = [bank.templates[i] for i in np.flatnonzero(labels == label)]
        # El id que sobrevive es el más chico del grupo, es decir el que apareció
        # primero: los `ecg_event` ya escritos con ese id siguen siendo válidos y
        # solo hay que reescribir los de las plantillas absorbidas.
        survivor = min(template.cluster_id for template in group)
        total = sum(template.count for template in group)
        blended = sum(
            (template.centroid * template.count for template in group),
            start=np.zeros(bank.beat_length, dtype=np.float32),
        )
        norm = float(np.linalg.norm(blended))
        centroid = (blended / norm).astype(np.float32) if norm > 0 else group[0].centroid
        merged.append(
            Template(
                cluster_id=survivor,
                centroid=centroid,
                count=total,
                sum_correlation=sum(template.sum_correlation for template in group),
                first_sample=min(template.first_sample for template in group),
                last_sample=max(template.last_sample for template in group),
            )
        )
        for template in group:
            if template.cluster_id != survivor:
                mapping[template.cluster_id] = survivor

    merged.sort(key=lambda template: template.cluster_id)
    return replace(bank, templates=tuple(merged)), mapping


# --------------------------------------------------------------------------- #
# Serialización
# --------------------------------------------------------------------------- #


def bank_to_state(bank: TemplateBank) -> tuple[dict[str, Any], bytes]:
    """`(metadata para JSONB, centroides para S3)`.

    Los centroides van aparte porque `select(Study)` trae todas las columnas: 40
    KB de vectores TOASTeados se leerían en cada listado y cada detalle de
    estudio, para nada.
    """
    state: dict[str, Any] = {
        "schemaVersion": STATE_SCHEMA_VERSION,
        "modelVersion": bank.model_version,
        "beatLength": bank.beat_length,
        "beatsSeen": bank.beats_seen,
        "unmatchedBeats": bank.unmatched_beats,
        "nextClusterId": bank.next_cluster_id,
        "consumedBatchIds": list(bank.consumed_batch_ids),
        "scoreFloor": bank.score_floor,
        "templates": [
            {
                "clusterId": template.cluster_id,
                "count": template.count,
                "sumCorrelation": round(template.sum_correlation, 6),
                "firstSample": template.first_sample,
                "lastSample": template.last_sample,
            }
            for template in bank.templates
        ],
    }
    if not bank.templates:
        return state, b""
    return state, bank.centroids().astype("<f4").tobytes()


def bank_from_state(state: dict[str, Any], blob: bytes, *, model_version: str) -> TemplateBank:
    """Reconstruye el banco. Devuelve uno vacío si el estado no es compatible.

    Un `schemaVersion` o un `modelVersion` distinto **no** se intenta migrar: los
    centroides viejos viven en otra representación y compararlos contra latidos
    nuevos daría distancias sin sentido. Empezar de cero es correcto y explícito.
    """
    length = int(state.get("beatLength", 0))
    compatible = (
        state.get("schemaVersion") == STATE_SCHEMA_VERSION
        and state.get("modelVersion") == model_version
        and length > 0
    )
    if not compatible:
        return TemplateBank(model_version=model_version, beat_length=length or 0)

    raw = state.get("templates") or []
    centroids = np.frombuffer(blob, dtype="<f4").reshape(-1, length) if blob else None
    templates: list[Template] = []
    for position, item in enumerate(raw):
        if centroids is None or position >= centroids.shape[0]:
            break
        templates.append(
            Template(
                cluster_id=int(item["clusterId"]),
                centroid=np.array(centroids[position], dtype=np.float32),
                count=int(item["count"]),
                sum_correlation=float(item.get("sumCorrelation", 0.0)),
                first_sample=int(item.get("firstSample", 0)),
                last_sample=int(item.get("lastSample", 0)),
            )
        )
    return TemplateBank(
        model_version=model_version,
        beat_length=length,
        templates=tuple(templates),
        beats_seen=int(state.get("beatsSeen", 0)),
        unmatched_beats=int(state.get("unmatchedBeats", 0)),
        next_cluster_id=int(state.get("nextClusterId", len(templates))),
        consumed_batch_ids=tuple(str(item) for item in state.get("consumedBatchIds", [])),
        score_floor=float(state.get("scoreFloor", 0.0)),
    )


def encode_centroids(blob: bytes) -> str:
    """Para tests y para el dump de diagnóstico; el camino real usa S3."""
    return base64.b64encode(blob).decode("ascii")
