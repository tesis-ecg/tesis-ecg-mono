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

## La frecuencia cardíaca

Un latido sinusal normal de una escalera no tiene la forma de uno en reposo, y
compararlo así lo hacía un hallazgo. Dos efectos: a más de ~110 lpm los ±250 ms
traen la T del latido anterior (a 155 lpm cae a −190 ms del R) y la P del
siguiente, y a cualquier frecuencia la propia T llega antes porque el QT se
acorta. Medido: una taquicardia de 60 s a 155 lpm dejaba 13 episodios de
"morfología atípica" y un foco recurrente de latidos normales con 9 % de carga;
en esfuerzo real (QT Database, `sel30x`), 77 episodios en 9 canales.

Lo corrigen tres cosas, ninguna en la representación guardada ni en cómo el
banco asigna (los `cluster_id` no se mueven):

- **El score mira el latido y no a sus vecinos** (`scoring_window`): la parte de
  la ventana que se compara se recorta con el R-R **esperado** —la mediana
  local de la prematuridad— y no con el del propio latido. Si la dominante se
  aprendió más rápido que eso, se recorta con el de ella. Un prematuro
  (`hrv.ECTOPIC_PREMATURITY`) no se recorta a la izquierda: la T del anterior
  encima de su P y de su QRS es lo que lo delata, y recortada desde ~125 lpm un
  supraventricular prematuro con el QRS normal no pasaba el umbral (a 130 lpm,
  0 de ~110; ahora todos).
- **Y lo compara con la dominante a su frecuencia** (`BeatRate`,
  `_rate_aware_correlation`): si llegó a tiempo y su frecuencia se aparta de la
  que aprendió la dominante (`Template.mean_expected_rr`), la repolarización de
  la dominante se comprime o se estira dentro de lo que el QT puede moverse. El
  QRS no se toca.
- **Un foco recurrente tiene que haber puntuado** (`is_recurrent`): la
  taquicardia sigue abriendo su plantilla, pero una cuyos latidos casi nunca
  pasan el umbral es una variante de la normal y no un foco. Cuenta solo para
  el encabezado del foco, se mide sobre los miembros que pudieron puntuar —los
  que llegaron mientras la plantilla no era la dominante— y una vez que una
  plantilla calificó no se deshace (`mark_reported`). Un latido suelto que
  puntuó se sigue informando en cualquier plantilla con 30 miembros
  (`TemplateBank.recurrent_ids`), que es como se ven los supraventriculares.
- **Cuando cambia la dominante** (`hand_over_dominance`), la que la pierde
  juzga los miembros que no pudieron puntuar por su centroide contra la nueva,
  y la que la gana deja de ser un foco: su encabezado se da de baja. Un
  bigeminismo con duplas de los primeros diez minutos dejaba el encabezado de
  la forma **normal** en la base y ninguno del ventricular, y el resultado
  dependía de dónde caían los bordes de bloque. Un foco cerca del 50 % puede
  cambiar de lugar con la normal más de una vez: cada cambio se rehace igual.

Lo que queda sin resolver: a ~200 lpm el detector de R pierde un latido de cada
dos (no acepta R-R menores de 300 ms). El R-R esperado queda en el doble del
real, los latidos que sí se detectan llegan con prematuridad ~0,5 y se comparan
sin adaptar, como prematuros: una taquicardia así sale en episodios MEDIUM
partidos. Es un problema del detector, no de la forma. Y el arranque
instantáneo de una taquicardia marca sus dos primeros latidos, que llegan antes
de que la mediana se adapte: así arranca una supraventricular.

La fracción de `is_recurrent` también deja sin encabezado a un foco real cuyos
latidos casi no puntúan: en MIT-BIH 223 una plantilla de 55 miembros (53 V)
con score medio 0,22 y 4 % por encima del umbral —sus V se parecen a las N en
ese canal: correlación mediana 0,90— no tiene encabezado; sus latidos que sí
puntúan salen en episodios. Y mientras un foco es la dominante, la forma normal
se puntúa contra él: sus latidos pueden salir como episodios (no como
encabezado, que se da de baja al recuperar el lugar).

Sobre MIT-BIH (48 registros) cuesta 7 de 7.712 latidos anormales detectados
—5 supraventriculares del 202, que no llegan prematuros— y saca 67 falsos. Por
bloques, como en producción, los anormales que llegan a un episodio quedan como
estaban (6.384 de 6.386) y los encabezados de foco bajan de 112 a 82.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.ml.contracts import Floats, Indices, Mask, Signal
from app.ml.hrv import ECTOPIC_PREMATURITY

#: Ventana del latido, en milisegundos alrededor del pico R. 250 ms hacia atrás
#: entran la P y el arranque del QRS; 250 ms hacia adelante, el final del QRS y
#: el ST. Es lo que necesita distinguir un ectópico ventricular (QRS ancho, T
#: opuesta) de un latido normal.
BEAT_PRE_MS = 250
BEAT_POST_MS = 250

#: Lo que se recorta de la ventana a frecuencia alta (`scoring_window`). El
#: score deja de mirar los primeros 300 ms después del R anterior —su QRS, su ST
#: y el grueso de su T— y los últimos 200 ms antes del R siguiente —su P—. Con
#: un R-R esperado de 550 ms o más (≤ 109 lpm) no se recorta nada: el score es
#: exactamente el de la ventana entera.
SCORE_PREVIOUS_BEAT_CLEARANCE_MS = 300
SCORE_NEXT_BEAT_CLEARANCE_MS = 200
#: Lo que nunca se recorta: el arranque del QRS antes del R y el QRS con el ST
#: después. Un ectópico ventricular se sigue viendo entero a cualquier frecuencia.
SCORE_MIN_PRE_MS = 60
SCORE_MIN_POST_MS = 120

#: El QRS, en ms alrededor del R: lo único que no se adapta nunca a la
#: frecuencia (`dissimilarity_to_dominant`). Después de `QRS_END_MS` está la
#: repolarización —ST y T—; antes de `QRS_START_MS`, la P.
QRS_START_MS = 60
QRS_END_MS = 60
#: Hasta dónde se adapta la repolarización de la dominante a la frecuencia de un
#: latido: factores de tiempo entre 1 y `(RR_latido / RR_dominante) ** esto`.
#: 1 es el extremo —la T se adelanta en proporción al R-R—; el QT real se acorta
#: menos (Bazett ~0,5, Fridericia ~0,33), y la búsqueda cubre todo el medio,
#: incluida la histéresis del QT, que tarda un minuto en alcanzar a la
#: frecuencia. La P se acerca al QRS con la raíz de ese factor: el PR también
#: se acorta con la frecuencia, pero bastante menos que el QT.
RATE_ADAPT_EXPONENT = 1.0
#: Paso de la grilla de factores, en logaritmo natural (~10 %). Es también la
#: zona muerta: un latido a menos de un paso de la frecuencia de la dominante se
#: compara sin adaptar.
RATE_ADAPT_LOG_STEP = 0.1
#: Cuánto tarda la repolarización en alcanzar un cambio de frecuencia. Después
#: de un esfuerzo, la T de un latido ya lento puede seguir siendo la de la
#: frecuencia de hace un minuto, así que se la deja comprimir hasta la del R-R
#: esperado más corto de este tramo. Es también el contexto izquierdo de cada
#: bloque en producción (`ml_analysis_context_seconds`), así que lo que mira
#: hacia atrás un latido nuevo está en la señal; con menos contexto, los
#: primeros de cada bloque miran lo que haya.
QT_HYSTERESIS_S = 60.0

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
    #: Miembros que se plegaron mientras la plantilla **no** era la dominante, y
    #: cuántos de ellos puntuaron por encima del umbral de anomalía
    #: (`count_anomalous`). Su cociente es lo que separa un foco de una variante
    #: de la forma normal: los latidos de una taquicardia sinusal abren
    #: plantilla propia —a 155 lpm la ventana ya no es la del reposo— pero casi
    #: ninguno puntúa. Sobre los que **pudieron** puntuar y no sobre todos: el
    #: score se mide contra la dominante, así que mientras una plantilla lo es
    #: sus miembros dan ~0 por construcción. Un foco que arrancó dominante —un
    #: bigeminismo con duplas al principio del estudio— se diluía con esos ceros
    #: y dejaba de informarse cuando la forma normal recuperaba el lugar.
    scored_count: int = 0
    anomalous_count: int = 0
    #: Si la plantilla ya calificó como foco (`is_recurrent`) alguna vez. Una
    #: vez que sí, su encabezado se sigue emitiendo con el conteo al día
    #: (`mark_reported`): la persistencia solo hace upsert de lo que se emite,
    #: y un encabezado que dejara de emitirse quedaba en la base congelado con
    #: un conteo viejo. `None` en un banco de antes de los contadores, hasta que
    #: el pipeline lo resuelve con la regla de entonces.
    reported: bool | None = False
    #: Suma del R-R esperado (`hrv.expected_rr`, en segundos) de los miembros
    #: que lo tenían, y cuántos eran. Su cociente es la frecuencia a la que se
    #: aprendió la forma (`mean_expected_rr`), que es contra lo que se adapta la
    #: repolarización de la dominante (`dissimilarity_to_dominant`). Sumas y no
    #: un promedio: el banco es un acumulador y un promedio no se pliega.
    expected_rr_sum: float = 0.0
    expected_rr_beats: int = 0

    @property
    def mean_expected_rr(self) -> float | None:
        """R-R medio al que se aprendió la plantilla, o None si no se sabe."""
        if self.expected_rr_beats <= 0 or self.expected_rr_sum <= 0:
            return None
        return self.expected_rr_sum / self.expected_rr_beats


def is_recurrent(template: Template, *, min_beats: int, min_anomalous_fraction: float) -> bool:
    """Si una plantilla que **no** es la dominante califica hoy como foco recurrente.

    Dos condiciones: miembros suficientes para no ser ruido disperso, y que de
    los que pudieron puntuar —los que se plegaron sin que la plantilla fuera la
    dominante, `Template.scored_count`— haya puntuado una fracción mínima. Sin
    la segunda, cualquier variante de la forma normal con 30 latidos —una
    taquicardia de esfuerzo— se informaba como foco con su carga. La fracción
    se mide recién con `min_beats` de esos miembros: con menos es ruido, y la
    decisión no se deshace (`mark_reported`). Con `min_anomalous_fraction` en 0
    queda solo el conteo, la regla de antes.

    Es solo el encabezado del foco: para agrupar episodios cuenta el conteo
    solo (`TemplateBank.recurrent_ids`).
    """
    if template.count < min_beats:
        return False
    if min_anomalous_fraction <= 0:
        return True
    return (
        template.scored_count >= min_beats
        and template.anomalous_count >= min_anomalous_fraction * template.scored_count
    )


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
    #: Clave del último tramo plegado al banco (`fold_key` de `analyze_batch`:
    #: hoy el id del lote, con el cursor de bloques la posición del bloque). Es
    #: una red de seguridad: volver a plegar ese mismo tramo **no** suma sus
    #: latidos otra vez, porque el banco es un acumulador y contarlos dos veces
    #: falsearía la carga (`burdenPct`) que el médico lee. Solo la última y no la
    #: lista de todas: los tramos se pliegan en orden, y la lista crecía una clave
    #: por lote (~5.760 por día de registro) dentro de `study.ml_state`, que
    #: viaja en cada `select(Study)`.
    last_fold_key: str | None = None
    score_floor: float = 0.0

    def centroids(self) -> Floats:
        if not self.templates:
            return np.empty((0, self.beat_length), dtype=np.float32)
        return np.stack([template.centroid for template in self.templates])

    def recurrent_ids(self, min_beats: int) -> frozenset[int]:
        """Clusters con miembros suficientes para no ser ruido disperso.

        Es lo que deja pasar un latido suelto como episodio
        (`episodes.group_beats`), y va **por conteo solo**, sin la fracción de
        `is_recurrent`. Un supraventricular prematuro tiene el QRS normal y cae
        en cualquier variante de la forma normal, no solo en la dominante: en
        esas plantillas casi ningún miembro puntúa, y con la fracción el
        supraventricular suelto que sí puntuó se descartaba (en MIT-BIH 213, sus
        10 supraventriculares informados pasaban a 0). Lo que la fracción tiene
        que filtrar es el encabezado del foco, no los latidos que puntuaron.
        """
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


def windows_ending_after(beats: BeatMatrix, boundary: int, sample_rate: int) -> Mask:
    """Por latido: verdadero si su ventana termina **después** de `boundary`.

    Es el complemento exacto de lo que `extract_beats` pudo sacar de una señal
    que terminaba en `boundary`: una ventana que no entraba entera ahí quedó
    afuera. Con eso el pipeline reparte los latidos entre bloques consecutivos
    sin perder ninguno: el R a menos de `BEAT_POST_MS` del final de un bloque no
    tiene ventana completa en ese bloque, y si el siguiente lo tratara como
    contexto por tener el R antes del borde no lo plegaría nadie — uno por
    borde, justo el ectópico que caiga ahí.
    """
    pre = int(round(BEAT_PRE_MS * sample_rate / 1000))
    return np.asarray(beats.rpeaks - pre + beat_length(sample_rate) > boundary, dtype=bool)


def select_beats(beats: BeatMatrix, keep: Mask) -> BeatMatrix:
    """Las filas de `beats` donde `keep` es verdadera, con su `beat_index` intacto.

    Es como el pipeline separa los latidos del contexto izquierdo de un bloque
    de los nuevos: el índice sigue apuntando a la serie R-R **completa**, así que
    la prematuridad de un latido nuevo se sigue leyendo contra los intervalos
    del contexto.
    """
    return BeatMatrix(
        beat_index=beats.beat_index[keep],
        rpeaks=beats.rpeaks[keep],
        waveforms=beats.waveforms[keep],
    )


def concat_beats(first: BeatMatrix, second: BeatMatrix) -> BeatMatrix:
    """Las filas de `first` seguidas de las de `second`, en ese orden.

    Para volver a juntar el contexto con la parte nueva después de separarlos:
    `select_beats` sobre la misma matriz y una máscara y su complemento, así que
    con `first` antes del borde y `second` después el resultado sigue ordenado
    por R.
    """
    return BeatMatrix(
        beat_index=np.concatenate((first.beat_index, second.beat_index)),
        rpeaks=np.concatenate((first.rpeaks, second.rpeaks)),
        waveforms=np.concatenate((first.waveforms, second.waveforms)),
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

    Es lo que corre si llega otra vez el último tramo plegado
    (`TemplateBank.last_fold_key`): sus latidos se puntúan contra el banco
    actual, pero no se cuentan de nuevo.
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
    fold_key: str | None = None,
    sample_offset: int = 0,
    expected_rr: Floats | None = None,
) -> tuple[TemplateBank, BeatAssignment]:
    """Pliega un lote al banco: asigna, crea plantillas nuevas y acumula.

    Dos pasadas a propósito. La primera resuelve de un matmul los latidos que
    caen en plantillas ya existentes —el 99 % en un registro normal—; la segunda
    recorre en Python solo los que sobraron, que son los candidatos a morfología
    nueva. Congelar los centroides durante la primera pasada además hace el
    resultado **independiente del orden** de los latidos dentro del lote.

    `expected_rr` es el R-R esperado de cada latido (`hrv.expected_rr`): se
    acumula en la plantilla que lo recibe para saber a qué frecuencia se
    aprendió (`Template.mean_expected_rr`). Sin él no se acumula nada.
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
        rates = np.empty(0, dtype=np.float64)
        if expected_rr is not None:
            rates = np.asarray(expected_rr[members], dtype=np.float64)
            rates = rates[np.isfinite(rates) & (rates > 0)]
        # `replace` y no un `Template` nuevo: `anomalous_count` lo suma
        # `count_anomalous` después de puntuar, y acá se conserva.
        templates[position] = replace(
            template,
            centroid=centroid,
            count=total,
            sum_correlation=template.sum_correlation
            + float(np.sum(np.clip(best_correlation[members], 0.0, 1.0))),
            first_sample=min(template.first_sample, int(samples.min())),
            last_sample=max(template.last_sample, int(samples.max())),
            expected_rr_sum=template.expected_rr_sum + float(rates.sum()),
            expected_rr_beats=template.expected_rr_beats + int(rates.size),
        )

    lookup = np.array([template.cluster_id for template in templates], dtype=np.int64)
    cluster_ids = np.where(slots >= 0, lookup[np.clip(slots, 0, None)], -1).astype(np.int64)
    dissimilarity = np.where(slots >= 0, 1.0 - np.clip(best_correlation, 0.0, 1.0), 1.0).astype(
        np.float32
    )

    updated = replace(
        bank,
        templates=tuple(templates),
        beats_seen=bank.beats_seen + beats.n_beats,
        unmatched_beats=bank.unmatched_beats + unmatched,
        next_cluster_id=next_id,
        last_fold_key=fold_key if fold_key is not None else bank.last_fold_key,
    )
    return updated, BeatAssignment(cluster_ids=cluster_ids, dissimilarity=dissimilarity)


def count_anomalous(bank: TemplateBank, cluster_ids: Indices, anomalous: Mask) -> TemplateBank:
    """Suma a cada plantilla sus miembros puntuables y cuántos puntuaron.

    `cluster_ids` es la asignación de los latidos que **se acaban de plegar**
    (`assign_and_update`) y `anomalous` si cada uno pasó el umbral. Los de la
    plantilla dominante de `bank` no suman a ninguno de los dos contadores
    (`Template.scored_count`): se puntuaron contra ella misma. Va aparte del
    pliegue porque el score se mide contra la dominante del banco ya
    actualizado, que todavía no existe mientras se asigna. Tiene la misma regla
    que el pliegue: un tramo que se vuelve a analizar con su `fold_key` ya
    plegado no suma —el pipeline no llama a esto—, o contaría sus latidos dos
    veces.
    """
    dominant = dominant_template(bank)
    scored = cluster_ids >= 0
    if dominant is not None:
        scored &= cluster_ids != dominant.cluster_id
    if not scored.any():
        return bank
    ids, counts = np.unique(cluster_ids[scored], return_counts=True)
    added = dict(zip(ids.tolist(), counts.tolist(), strict=True))
    hit_ids, hit_counts = np.unique(cluster_ids[scored & anomalous], return_counts=True)
    hits = dict(zip(hit_ids.tolist(), hit_counts.tolist(), strict=True))
    return replace(
        bank,
        templates=tuple(
            replace(
                template,
                scored_count=template.scored_count + added[template.cluster_id],
                anomalous_count=template.anomalous_count + hits.get(template.cluster_id, 0),
            )
            if template.cluster_id in added
            else template
            for template in bank.templates
        ),
    )


def mark_reported(
    bank: TemplateBank, *, min_beats: int, min_anomalous_fraction: float
) -> TemplateBank:
    """Marca las plantillas que **no** son la dominante y ya calificaron como foco.

    La marca no se deshace (`Template.reported`). La fracción de `is_recurrent`
    no es monótona —baja cuando la plantilla suma miembros que no puntúan—, y
    la persistencia solo hace upsert de los encabezados que se emiten: uno que
    dejara de emitirse quedaba en la base con el conteo de cuando calificó,
    mientras la plantilla seguía creciendo. Con la marca, un foco informado se
    sigue informando con su conteo al día.

    Un banco de antes de los contadores trae la marca en `None`, y se resuelve
    acá con la regla de entonces —no dominante con `min_beats` miembros—, que es
    justo el encabezado que ese estudio ya tiene escrito. Desde ahí, una
    plantilla que todavía no calificó se juzga por los miembros que se plieguen
    de ahora en más. Pura y determinista: aplicarla dos veces no cambia nada.
    """
    dominant = dominant_template(bank)
    changed = False
    templates: list[Template] = []
    for template in bank.templates:
        is_dominant = dominant is not None and template.cluster_id == dominant.cluster_id
        reported = template.reported
        if reported is None:
            reported = not is_dominant and template.count >= min_beats
        elif not reported and not is_dominant:
            reported = is_recurrent(
                template, min_beats=min_beats, min_anomalous_fraction=min_anomalous_fraction
            )
        if reported != template.reported:
            changed = True
            template = replace(template, reported=reported)
        templates.append(template)
    return replace(bank, templates=tuple(templates)) if changed else bank


def hand_over_dominance(
    before: TemplateBank,
    after: TemplateBank,
    *,
    sample_rate: int,
    match_threshold: float,
    anomaly_score_min: float,
) -> tuple[TemplateBank, tuple[int, ...]]:
    """Rehace los contadores cuando la dominante cambia de una plantilla a otra.

    Los miembros que se plegaron mientras una plantilla era la dominante no
    puntuaron (`count_anomalous`): se medían contra ella misma. Un foco que
    arrancó dominante —una dupla de V-V-N durante los primeros diez minutos—
    llegaba a la recuperación de la forma normal con `scored_count` casi en
    cero, y sin 30 latidos puntuados nunca se informaba; mientras tanto la
    forma normal, puntuada contra la V, calificaba como foco y su encabezado
    quedaba en la base, congelado. Y el resultado dependía de dónde caían los
    bordes de bloque.

    Al cambiar la dominante de A a B:

    - A —la que pierde— juzga sus miembros sin puntuar por su centroide contra
      B, a su frecuencia y como si llegaran a tiempo (el score más bajo
      posible: sin prematuridad): todos anómalos si el centroide lo es, ninguno
      si no. Desde ahí se juzga como cualquiera (`mark_reported`).
    - B —la que gana— vuelve a cero: lo que había puntuado fue contra A. Si ya
      se informaba como foco, su encabezado se retira (lo que se devuelve: los
      `cluster_id` cuyo encabezado hay que dar de baja). Es el latido del
      paciente.
    """
    old, new = dominant_template(before), dominant_template(after)
    if old is None or new is None or old.cluster_id == new.cluster_id:
        return after, ()
    loser = next((t for t in after.templates if t.cluster_id == old.cluster_id), None)
    retracted: tuple[int, ...] = ()
    templates: list[Template] = []
    for template in after.templates:
        if template.cluster_id == new.cluster_id:
            if template.reported:
                retracted = (template.cluster_id,)
            template = replace(template, scored_count=0, anomalous_count=0, reported=False)
        elif loser is not None and template.cluster_id == loser.cluster_id:
            unscored = max(template.count - template.scored_count, 0)
            if unscored:
                expected = template.mean_expected_rr
                centroid = BeatMatrix(
                    beat_index=np.zeros(1, dtype=np.int64),
                    rpeaks=np.zeros(1, dtype=np.int64),
                    waveforms=template.centroid[np.newaxis, :].astype(np.float32),
                )
                score = anomaly_score(
                    dissimilarity_to_dominant(
                        after,
                        centroid,
                        BeatRate(
                            expected_rr=np.array([expected or np.nan], dtype=np.float32),
                            prematurity=np.ones(1, dtype=np.float32),
                            sample_rate=sample_rate,
                        ),
                    ),
                    np.ones(1, dtype=np.float32),
                    match_threshold=match_threshold,
                )
                anomalous = bool(score[0] >= anomaly_score_min)
                template = replace(
                    template,
                    scored_count=template.scored_count + unscored,
                    anomalous_count=template.anomalous_count + (unscored if anomalous else 0),
                )
        templates.append(template)
    return replace(after, templates=tuple(templates)), retracted


def dominant_template(bank: TemplateBank) -> Template | None:
    """La plantilla del paciente: la que más miembros tiene.

    No hace falta ninguna heurística más elaborada. Un Holter tiene más del 90 %
    de sus latidos en una sola morfología —la normal de ese paciente— y cualquier
    foco ectópico, por activo que sea, queda muy por debajo.
    """
    if not bank.templates:
        return None
    return max(bank.templates, key=lambda template: template.count)


@dataclass(frozen=True, slots=True)
class ScoringWindow:
    """Por latido, el tramo `[start, stop)` de su ventana que el score compara.

    En muestras de la ventana de `extract_beats` (0 a `beat_length`). Con
    `start == 0` y `stop == beat_length` es la ventana entera.
    """

    start: Indices
    stop: Indices


def scoring_window(expected_rr_s: Floats, sample_rate: int) -> ScoringWindow:
    """La parte de cada ventana que pertenece al latido, según su R-R **esperado**.

    `expected_rr_s` es, por latido, el R-R con que se lo esperaba
    (`hrv.expected_rr`, la referencia de la prematuridad). Antes del R se deja
    afuera lo que cae en los primeros `SCORE_PREVIOUS_BEAT_CLEARANCE_MS` del
    latido anterior y después, los últimos `SCORE_NEXT_BEAT_CLEARANCE_MS` antes
    del siguiente; nunca menos de `SCORE_MIN_PRE_MS` / `SCORE_MIN_POST_MS`.

    El esperado y no el propio, a propósito. Recortar con el R-R del latido
    borraba la T del latido anterior justo de la ventana de un supraventricular
    prematuro, que es lo que lo delata: medido sobre los 48 registros de
    MIT-BIH, 59 latidos anormales detectados menos (en el 202, 10 de sus 68
    supraventriculares). Con el esperado, un prematuro conserva la ventana del
    ritmo en el que cayó —la de reposo, entera— y un latido de una taquicardia
    sostenida, que llega cuando se lo esperaba, se mira sin sus vecinos. Sin
    referencia (NaN, el primer latido de la serie) la ventana es la entera.
    """
    pre_full = int(round(BEAT_PRE_MS * sample_rate / 1000))
    length = beat_length(sample_rate)
    rr_ms = np.asarray(expected_rr_s, dtype=np.float64) * 1000.0
    known = np.isfinite(rr_ms) & (rr_ms > 0)
    pre_ms = np.clip(rr_ms - SCORE_PREVIOUS_BEAT_CLEARANCE_MS, SCORE_MIN_PRE_MS, BEAT_PRE_MS)
    post_ms = np.clip(rr_ms - SCORE_NEXT_BEAT_CLEARANCE_MS, SCORE_MIN_POST_MS, BEAT_POST_MS)
    full_pre = ~known | (pre_ms >= BEAT_PRE_MS)
    full_post = ~known | (post_ms >= BEAT_POST_MS)
    pre = np.round(np.where(full_pre, BEAT_PRE_MS, pre_ms) * sample_rate / 1000.0).astype(np.int64)
    post = np.round(np.where(full_post, BEAT_POST_MS, post_ms) * sample_rate / 1000.0)
    return ScoringWindow(
        start=np.where(full_pre, 0, np.clip(pre_full - pre, 0, pre_full)).astype(np.int64),
        stop=np.where(
            full_post, length, np.clip(pre_full + post.astype(np.int64), pre_full + 1, length)
        ).astype(np.int64),
    )


@dataclass(frozen=True, slots=True)
class BeatRate:
    """Por latido de una `BeatMatrix`, el ritmo en el que cayó.

    `expected_rr` es el R-R con que se lo esperaba, en segundos
    (`hrv.expected_rr`; NaN sin referencia), y `prematurity` cuánto se adelantó
    (`hrv.prematurity`). Es lo que el score necesita para no confundir la
    frecuencia con la forma (`dissimilarity_to_dominant`).
    """

    expected_rr: Floats
    prematurity: Floats
    sample_rate: int


def dissimilarity_to_dominant(
    bank: TemplateBank, beats: BeatMatrix, rate: BeatRate | None = None
) -> Floats:
    """`1 − correlación` de cada latido contra la plantilla **dominante**.

    Es la disimilitud que importa y no la que hay contra la plantilla asignada.
    Medirla contra la asignada es circular: el banco le abre plantilla propia a
    cada morfología nueva, así que **todo latido correlaciona ~1,0 con la suya**
    por construcción y el score de anomalía daría 0 para todo el mundo —
    incluidos los nueve ectópicos que el banco acababa de aislar perfectamente en
    su propio cluster.

    Contra la dominante la pregunta vuelve a ser la del método: *¿cuánto se
    parece este latido a los normales de este paciente?* Con `rate`, a los
    normales **a esta frecuencia** (`_rate_aware_correlation`). Sin él es la
    comparación de la ventana entera, sin más.
    """
    dominant = dominant_template(bank)
    if dominant is None or beats.n_beats == 0:
        return np.ones(beats.n_beats, dtype=np.float32)
    correlation = beats.waveforms @ dominant.centroid
    if rate is not None:
        correlation = _rate_aware_correlation(beats, dominant, rate, correlation)
    return np.asarray(np.clip(1.0 - correlation, 0.0, 2.0), dtype=np.float32)


def _rate_aware_correlation(
    beats: BeatMatrix, dominant: Template, rate: BeatRate, full: Floats
) -> Floats:
    """La correlación con la dominante, sin lo que cambia la frecuencia y no la forma.

    Dos efectos de la frecuencia que no son forma:

    1. **Los vecinos.** A más de ~110 lpm los ±250 ms traen la T del latido
       anterior y la P del siguiente: se compara solo el tramo del latido
       (`scoring_window`), con la forma recentrada y renormalizada en él. El
       tramo sale del R-R más corto entre el que se esperaba para el latido y
       el que aprendió la dominante: en una dominante de esfuerzo los vecinos
       están en el centroide.
    2. **Su propia repolarización.** El QT se acorta con la frecuencia: a 120
       lpm la T de un latido sinusal llega ~70 ms antes que en reposo, y con la
       ventana limpia igual correlacionaba 0,85 con la dominante. Si el latido
       llegó a tiempo (prematuridad ≥ `hrv.ECTOPIC_PREMATURITY`) y su
       frecuencia se aparta de la que aprendió la dominante
       (`Template.mean_expected_rr`) más que un paso de la grilla, se lo compara
       además contra la dominante con el ST-T comprimido —o estirado— por
       factores entre 1 y el cociente de los R-R (`_stretched`; la P, con su
       raíz), y vale la mejor. Comprimido, hasta el R-R más corto de los últimos
       `QT_HYSTERESIS_S`: el QT tarda del orden de un minuto en alcanzar a la
       frecuencia, y al bajar de una escalera la T sigue llegando temprano un
       rato. El QRS no se toca nunca: un ectópico ventricular sigue siendo un
       QRS distinto a cualquier frecuencia, y un prematuro se compara como está,
       que es como se lo reconoce. Lo que se relaja es solo **cuándo** llega la
       repolarización de un latido que llegó cuando se lo esperaba.

    Una dominante sin frecuencia conocida (un banco de antes de que se
    acumulara) se compara sin adaptar.
    """
    waveforms = beats.waveforms
    n_beats, length = waveforms.shape
    if rate.expected_rr.shape != (n_beats,) or rate.prematurity.shape != (n_beats,):
        raise ValueError("BeatRate tiene que tener un valor por latido")
    learned = dominant.mean_expected_rr
    expected = np.asarray(rate.expected_rr, dtype=np.float64)
    # La ventana es la del más rápido de los dos: el latido o la dominante. Una
    # dominante aprendida en esfuerzo —un estudio que arranca caminando rápido—
    # trae en su centroide la T del latido anterior y la P del siguiente, y un
    # latido de reposo comparado con eso en la ventana entera puntuaba (a 185
    # lpm, los de la recuperación daban 0,37-0,46). Hasta ~109 lpm (R-R de 550
    # ms) la dominante no recorta nada.
    window = scoring_window(
        rate.expected_rr
        if learned is None
        else np.fmin(rate.expected_rr, np.float32(learned)).astype(np.float32),
        rate.sample_rate,
    )
    # Un prematuro conserva el lado izquierdo entero: la T del latido anterior
    # encima de su P y de su QRS es lo que lo delata. Recortado con el R-R
    # esperado, desde ~125 lpm ya no quedaba nada de ella y un supraventricular
    # prematuro con el QRS normal no podía pasar el umbral solo por la
    # prematuridad (a 130 lpm, 0 de ~110 detectados).
    window = ScoringWindow(
        start=np.where(rate.prematurity < ECTOPIC_PREMATURITY, 0, window.start),
        stop=window.stop,
    )
    result = full.astype(np.float64)
    clipped = (window.start > 0) | (window.stop < length)
    if clipped.any():
        result[clipped] = _correlations(
            waveforms[clipped], dominant.centroid, window.start[clipped], window.stop[clipped]
        )

    if learned is None:
        return np.asarray(result, dtype=np.float32)
    fastest = np.fmin(
        expected,
        _trailing_min(expected, beats.rpeaks, int(QT_HYSTERESIS_S * rate.sample_rate)),
    )
    adapt = (
        np.isfinite(expected)
        & (expected > 0)
        & np.isfinite(fastest)
        & (fastest > 0)
        & (rate.prematurity >= ECTOPIC_PREMATURITY)
    )
    # En logaritmo del factor: hasta dónde se puede estirar (latido más lento
    # que la dominante) y hasta dónde comprimir (más rápido, ahora o hace
    # menos de `QT_HYSTERESIS_S`). Cero donde no se adapta.
    stretch = np.zeros(n_beats, dtype=np.float64)
    compress = np.zeros(n_beats, dtype=np.float64)
    stretch[adapt] = RATE_ADAPT_EXPONENT * np.log(expected[adapt] / learned)
    compress[adapt] = -RATE_ADAPT_EXPONENT * np.log(fastest[adapt] / learned)
    reach = float(max(np.max(stretch), np.max(compress), 0.0))
    steps = int(np.floor(reach / RATE_ADAPT_LOG_STEP + 1e-9))
    center = int(round(BEAT_PRE_MS * rate.sample_rate / 1000))
    qrs = (
        center - int(round(QRS_START_MS * rate.sample_rate / 1000)),
        center + int(round(QRS_END_MS * rate.sample_rate / 1000)),
    )
    for step in range(1, steps + 1):
        for sign, limit_log in ((-1.0, compress), (1.0, stretch)):
            # Los factores entre 1 y el extremo de cada latido, en pasos de la
            # grilla: con signo, porque un latido más lento que la dominante
            # (la dominante se aprendió en una caminata) estira en vez de
            # comprimir.
            rows = np.flatnonzero(limit_log >= step * RATE_ADAPT_LOG_STEP - 1e-9)
            if rows.size == 0:
                continue
            reference, (first, last) = _stretched(
                dominant.centroid, float(np.exp(sign * step * RATE_ADAPT_LOG_STEP)), qrs
            )
            result[rows] = np.maximum(
                result[rows],
                _correlations(
                    waveforms[rows],
                    reference,
                    np.maximum(window.start[rows], first),
                    np.minimum(window.stop[rows], last),
                ),
            )
    return np.asarray(result, dtype=np.float32)


def _trailing_min(
    values: NDArray[np.float64], positions: Indices, span: int
) -> NDArray[np.float64]:
    """Por elemento, el mínimo de `values` en `[posición − span, posición]` (sin NaN).

    Con una tabla de mínimos por potencias de dos: O(n log n) sin recorrer en
    Python. NaN donde no hay ningún valor conocido en el tramo.
    """
    count = values.size
    if count == 0:
        return values.copy()
    order = np.argsort(positions, kind="stable")
    ordered = values[order]
    sorted_positions = positions[order]
    first = np.searchsorted(sorted_positions, sorted_positions - span, side="left")
    last = np.arange(count)
    tables = [ordered]
    while 2 ** len(tables) <= count:
        width = 2 ** (len(tables) - 1)
        tables.append(np.fmin(tables[-1][:-width], tables[-1][width:]))
    level = np.floor(np.log2(last - first + 1)).astype(np.int64)
    result = np.empty(count, dtype=np.float64)
    for depth in np.unique(level).tolist():
        rows = np.flatnonzero(level == depth)
        table = tables[depth]
        result[rows] = np.fmin(table[first[rows]], table[last[rows] - 2**depth + 1])
    unsorted = np.empty(count, dtype=np.float64)
    unsorted[order] = result
    return unsorted


def _stretched(
    centroid: Floats, factor: float, qrs: tuple[int, int]
) -> tuple[NDArray[np.float64], tuple[int, int]]:
    """El centroide con la frecuencia cambiada por `factor`, fuera del QRS.

    Lo posterior al QRS (`qrs[1]`) se escala en el tiempo por `factor` y lo
    anterior (`qrs[0]`) por su raíz: con `factor` < 1, la T llega antes y más
    angosta y la P se acerca un poco, como a más frecuencia. Devuelve también el
    tramo `[desde, hasta)` donde vale: comprimido, los bordes de la ventana
    saldrían de muestras que el centroide no tiene —se guardan ±250 ms—, y eso
    no se compara.
    """
    length = centroid.size
    start, end = qrs
    atrial = float(np.sqrt(factor))
    positions = np.arange(length, dtype=np.float64)
    source = np.where(positions > end, end + (positions - end) / factor, positions)
    source = np.where(positions < start, start - (start - positions) / atrial, source)
    stretched = np.interp(source, positions, centroid.astype(np.float64))
    if factor >= 1.0:
        return stretched, (0, length)
    first = max(0, int(np.ceil(start - start * atrial)))
    last = min(length, int(np.floor(end + (length - 1 - end) * factor)) + 1)
    return stretched, (first, last)


def _correlations(
    waveforms: Floats, reference: NDArray[Any], start: Indices, stop: Indices
) -> NDArray[np.float64]:
    """Correlación de cada fila con `reference` en su tramo `[start, stop)`.

    En el tramo, la fila y la referencia se recentran por su mediana y se
    renormalizan: la representación de `extract_beats` restringida a él, así
    que con el tramo entero da el producto punto de siempre. Se agrupan las
    filas por tramo: dentro de un bloque el R-R esperado se mueve poco y cada
    tramo distinto es un solo matmul.
    """
    result = np.zeros(waveforms.shape[0], dtype=np.float64)
    if waveforms.shape[0] == 0:
        return result
    length = waveforms.shape[1]
    low_bounds = np.clip(start, 0, length - 1)
    bounds = np.stack((low_bounds, np.clip(stop, low_bounds + 1, length)), axis=1)
    unique, inverse = np.unique(bounds, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    for position, (low, high) in enumerate(unique.tolist()):
        members = np.flatnonzero(inverse == position)
        segments = waveforms[members, low:high].astype(np.float64)
        segments -= np.median(segments, axis=1, keepdims=True)
        target = np.asarray(reference[low:high], dtype=np.float64)
        target = target - np.median(target)
        scale = np.linalg.norm(segments, axis=1) * float(np.linalg.norm(target))
        # Un tramo plano no se parece a nada: correlación 0, como el latido que
        # no matcheó ninguna plantilla.
        result[members] = np.where(
            scale > 0, (segments @ target) / np.where(scale > 0, scale, 1.0), 0.0
        )
    return result


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

    Corre **al finalizar el estudio**, y sobre ≤ 40 centroides — no sobre
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
                scored_count=sum(template.scored_count for template in group),
                anomalous_count=sum(template.anomalous_count for template in group),
                expected_rr_sum=sum(template.expected_rr_sum for template in group),
                expected_rr_beats=sum(template.expected_rr_beats for template in group),
                reported=_merged_reported(group),
            )
        )
        for template in group:
            if template.cluster_id != survivor:
                mapping[template.cluster_id] = survivor

    merged.sort(key=lambda template: template.cluster_id)
    return replace(bank, templates=tuple(merged)), mapping


def _merged_reported(group: list[Template]) -> bool | None:
    """La marca de foco de un grupo fundido: informado si alguno lo estaba.

    Sin ninguno informado y alguno de un banco viejo (`None`), queda sin
    resolver: `mark_reported` lo resuelve con el conteo fundido.
    """
    if any(template.reported for template in group):
        return True
    if any(template.reported is None for template in group):
        return None
    return False


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
        "lastFoldKey": bank.last_fold_key,
        "scoreFloor": bank.score_floor,
        "templates": [
            {
                "clusterId": template.cluster_id,
                "count": template.count,
                "sumCorrelation": round(template.sum_correlation, 6),
                "firstSample": template.first_sample,
                "lastSample": template.last_sample,
                "scoredCount": template.scored_count,
                "anomalousCount": template.anomalous_count,
                "reported": template.reported,
                "expectedRrSum": round(template.expected_rr_sum, 6),
                "expectedRrBeats": template.expected_rr_beats,
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
        # Un banco de antes de los contadores no sabe cuántos de sus miembros
        # pudieron puntuar ni cuántos puntuaron: arrancan en cero y la marca de
        # foco queda sin resolver (`None`) hasta que `mark_reported` le aplica
        # la regla de entonces. Así un encabezado que ese estudio ya tiene
        # escrito se sigue actualizando, y las demás plantillas se juzgan por
        # lo que se pliegue de acá en más. Sin `scoredCount` también un
        # `anomalousCount` suelto se ignora: contaba los miembros de la
        # dominante, que no pueden puntuar.
        legacy = "scoredCount" not in item
        reported = item.get("reported")
        templates.append(
            Template(
                cluster_id=int(item["clusterId"]),
                centroid=np.array(centroids[position], dtype=np.float32),
                count=int(item["count"]),
                sum_correlation=float(item.get("sumCorrelation", 0.0)),
                first_sample=int(item.get("firstSample", 0)),
                last_sample=int(item.get("lastSample", 0)),
                scored_count=0 if legacy else int(item["scoredCount"]),
                anomalous_count=0 if legacy else int(item.get("anomalousCount", 0)),
                reported=None if legacy or reported is None else bool(reported),
                # Sin frecuencia conocida la dominante no se adapta: el score es
                # el de la ventana recortada sola hasta que se pliegue algo.
                expected_rr_sum=float(item.get("expectedRrSum", 0.0)),
                expected_rr_beats=int(item.get("expectedRrBeats", 0)),
            )
        )
    return TemplateBank(
        model_version=model_version,
        beat_length=length,
        templates=tuple(templates),
        beats_seen=int(state.get("beatsSeen", 0)),
        unmatched_beats=int(state.get("unmatchedBeats", 0)),
        next_cluster_id=int(state.get("nextClusterId", len(templates))),
        # Un estado viejo trae `consumedBatchIds` (la lista entera): se ignora y
        # el próximo `bank_to_state` lo reemplaza. Uno de antes del cursor de
        # bloques trae la clave con su nombre anterior, `lastFoldedBatchId`.
        last_fold_key=_last_fold_key(state),
        score_floor=float(state.get("scoreFloor", 0.0)),
    )


def _last_fold_key(state: dict[str, Any]) -> str | None:
    value = state.get("lastFoldKey") or state.get("lastFoldedBatchId")
    return str(value) if value else None


def encode_centroids(blob: bytes) -> str:
    """Para tests y para el dump de diagnóstico; el camino real usa S3."""
    return base64.b64encode(blob).decode("ascii")
