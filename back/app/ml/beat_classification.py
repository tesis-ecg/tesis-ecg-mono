"""Clasificación por latido (N, S, V, X, Q) y los hallazgos que salen de ella.

Corre dentro de `pipeline.analyze_batch`, detrás del gate de calidad y del
banco de plantillas: es el único lugar que tiene a la vez qué señal es GOOD,
cuánto se parece cada latido a la morfología dominante del paciente, el R-R
esperado y la señal de análisis sobre la que se mide el QRS.

**Cada latido detectado queda con una etiqueta:**

- `X` el R cae en una ventana que no es GOOD (ruido, electrodo, empalme).
- `Q` es GOOD pero el latido no se pudo extraer, o no se pudo medir su QRS, o su
  ruido de alta frecuencia es una fracción grande de su amplitud
  (`BeatThresholds.max_noise_ratio`): no se adivina nada.
- `V` ventricular: QRS de `wide_qrs_ms` o más **con una dominante angosta** y
  una forma algo distinta de la dominante (`dissimilarity_v_soft`); o una forma
  muy distinta (`dissimilarity_v_strong`); o distinta (`dissimilarity_v`) con
  prematuridad o QRS ancho. Si el latido llegó prematuro y lo sigue una pausa
  compensatoria alcanza con una diferencia de forma menor
  (`dissimilarity_v_soft`): la pausa suma evidencia.
- `S` supraventricular: prematuro (R-R previo <= 80 % del esperado, el mismo
  corte con que la VFC edita la serie NN) con el QRS angosto y forma parecida a
  la dominante.
- `N` el resto.

**Un paciente con bloqueo de rama** tiene la dominante ancha: ahí un QRS ancho
no vuelve ventricular a nadie (todos serían V) y el ancho sostenido sale como
`wide_qrs_sustained` (`conduction.py`). Con la dominante desconocida (pocos
latidos medidos) el ancho tampoco clasifica.

**Qué no hace.** No distingue una taquicardia sinusal de una supraventricular:
las dos son latidos a tiempo y de la misma forma, y una S es prematura contra
la mediana local, que a los pocos latidos de una taquicardia sostenida ya se
adaptó. Por eso `svt_run` es una corrida corta; la sostenida la cubre
`tachycardia` (`arrhythmia.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from app.ml import beat_labels as bl
from app.ml.conduction import (
    ConductionInput,
    conduction_findings,
    empty_p_waves,
    measure_p_waves,
    p_filter,
)
from app.ml.contracts import EpisodeBudget, Finding, Floats, Indices, Mask, Signal
from app.ml.ectopy import ectopy_findings
from app.ml.hrv import ECTOPIC_PREMATURITY, RRSeries, build_rr
from app.ml.hrv import expected_rr as expected_rr_series
from app.ml.qrs import QRS_WIDTH_VALIDATED, WIDE_QRS_MS, QrsDelineation, delineate_qrs

#: Un R-R siguiente que alarga el ciclo al menos esto sobre lo esperado es la
#: pausa compensatoria de un ectópico (completa sería ~1,4: el ciclo entero
#: menos el adelanto). Solo suma evidencia, nunca decide sola.
COMPENSATORY_RATIO: Final = 1.15

#: Disimilitud de forma (1 - correlación con la dominante) a partir de la cual un
#: latido es de otra forma. Es `2 * (1 - match_threshold)` del banco de
#: plantillas: donde el score de morfología satura (`morphology.anomaly_score`).
DISSIMILARITY_V: Final = 0.20
#: La misma, con la prematuridad y la pausa compensatoria a favor.
DISSIMILARITY_V_SOFT: Final = 0.10
#: Con una diferencia de forma tan grande (correlación menor de 0,5) alcanza sola.
#: Entre `DISSIMILARITY_V` y esto la forma distinta necesita otra evidencia
#: —prematuridad o QRS ancho—: un paciente cuyos latidos normales cambian de
#: forma con la respiración o con la línea de base (MIT-BIH 230: ~40 % de sus
#: latidos normales a más de 0,2 de la dominante) sería un 40 % ventricular.
DISSIMILARITY_V_STRONG: Final = 0.50

#: Guarda de ruido local: ruido blanco equivalente sobre amplitud pico a pico
#: del QRS. Con electrodos secos 0,05 es un chaleco ruidoso pero legible; por
#: encima, la forma que se compara es mayormente ruido.
MAX_NOISE_RATIO: Final = 0.10

#: Un "latido" detectado cuyo complejo mide menos de esta fracción de la amplitud
#: de la dominante no es un QRS: es una P, una T o ruido que el detector tomó por
#: R (en un R-R largo, sin QRS, el umbral adaptativo baja hasta una P). Queda Q y
#: sale de la serie sobre la que se razona la conducción.
MIN_RELATIVE_AMPLITUDE: Final = 0.30

#: Latidos normales que hacen falta en el bloque para buscar ondas P y reglas de conducción.
MIN_NORMAL_FOR_CONDUCTION: Final = 8

#: Latidos de la morfología dominante con QRS medido que hacen falta para decir
#: si la dominante es angosta.
DOMINANT_MIN_BEATS: Final = 8

#: Cuánto más ancho que la mediana de la dominante tiene que ser un QRS para
#: que el ancho solo lo vuelva V. La medición oscila unos 10-20 ms de latido a
#: latido (la mitad de ellos justo contra los 120 ms en un paciente de QRS de
#: 100): sin margen, un normal con un QRS medido en 120 sería ventricular.
WIDE_MARGIN_MS: Final = 40.0


@dataclass(frozen=True, slots=True)
class BeatThresholds:
    """Umbrales de la clasificación. `pipeline.build_config` los arma de los settings."""

    wide_qrs_ms: float = WIDE_QRS_MS
    premature_ratio: float = ECTOPIC_PREMATURITY
    compensatory_ratio: float = COMPENSATORY_RATIO
    dissimilarity_v: float = DISSIMILARITY_V
    dissimilarity_v_soft: float = DISSIMILARITY_V_SOFT
    dissimilarity_v_strong: float = DISSIMILARITY_V_STRONG
    max_noise_ratio: float = MAX_NOISE_RATIO
    dominant_min_beats: int = DOMINANT_MIN_BEATS
    wide_margin_ms: float = WIDE_MARGIN_MS
    min_relative_amplitude: float = MIN_RELATIVE_AMPLITUDE


@dataclass(frozen=True, slots=True)
class BeatClassification:
    """Salida del bloque. Las coordenadas son **relativas al lote**."""

    #: Por R del bloque (contexto y parte nueva), código ASCII de la etiqueta.
    labels: np.ndarray
    #: Registros persistibles de los latidos de la parte nueva (`owned`), con
    #: `sample_index` relativo al lote: el pipeline lo traslada.
    records: bl.BeatLabelRecords
    findings: tuple[Finding, ...]


def classify_block(
    *,
    cleaned: Signal,
    amplitude: Signal,
    rpeaks: Indices,
    analyzable: Mask,
    rr: RRSeries,
    expected_rr: Floats,
    prematurity: Floats,
    dissimilarity: Floats,
    morphology_scores: Floats,
    dominant: Mask,
    owned: Mask,
    firmware_peaks: Indices,
    firmware_tolerance: int,
    origin: int,
    sample_rate: int,
    budget: EpisodeBudget,
    thresholds: BeatThresholds,
    classifier: str = bl.CLASSIFIER_VERSION,
) -> BeatClassification:
    """Etiqueta cada R del bloque y arma los hallazgos de ectopia y conducción.

    Los arreglos por latido (`dissimilarity`, `morphology_scores`, `dominant`)
    tienen el largo de `rpeaks`; `dissimilarity` es NaN donde el latido no se
    pudo extraer (la ventana de ±250 ms no cae entera en señal GOOD).
    `owned` marca los latidos que informa este bloque: los del contexto entran
    en los patrones (un bigeminismo no se corta en el borde) pero no se
    persisten.
    """
    n = int(rpeaks.size)
    if n == 0:
        return BeatClassification(
            labels=np.empty(0, dtype=np.uint8), records=bl.empty_records(), findings=()
        )

    good = analyzable[np.clip(rpeaks, 0, analyzable.size - 1)]
    qrs = delineate_qrs(amplitude, rpeaks, analyzable, sample_rate)
    width = qrs.width_ms(sample_rate)
    quiet = _quiet_enough(qrs, thresholds.max_noise_ratio)
    reference_amplitude = _reference_amplitude(qrs, dominant, thresholds.dominant_min_beats)
    with np.errstate(invalid="ignore"):
        not_a_qrs = qrs.valid & (
            qrs.amplitude_mv < thresholds.min_relative_amplitude * reference_amplitude
        )
    classifiable = good & qrs.valid & quiet & np.isfinite(dissimilarity) & ~not_a_qrs

    wide = (
        (width >= thresholds.wide_qrs_ms) & np.isfinite(width)
        if QRS_WIDTH_VALIDATED
        else np.zeros(n, dtype=bool)
    )
    dominant_widths = width[dominant & np.isfinite(width)]
    dominant_median = (
        float(np.median(dominant_widths))
        if dominant_widths.size >= thresholds.dominant_min_beats
        else np.inf
    )
    dominant_narrow = dominant_median < thresholds.wide_qrs_ms
    # Ancho que solo ya vuelve V: pasa el corte y se aparta de la dominante.
    wide_for_v = wide & (width >= dominant_median + thresholds.wide_margin_ms)
    # La prematuridad solo vale si el R-R que trae al latido es válido: uno que
    # cruza señal no GOOD puede venir de un R falso detectado en el ruido, y
    # haría S al primer latido bueno que le sigue.
    preceded_by_valid = np.zeros(n, dtype=bool)
    if rr.valid.size == n - 1:
        preceded_by_valid[1:] = rr.valid
    premature = (
        preceded_by_valid & np.isfinite(prematurity) & (prematurity <= thresholds.premature_ratio)
    )
    following = np.concatenate((prematurity[1:], [np.nan]))
    compensatory = np.isfinite(following) & (following >= thresholds.compensatory_ratio)
    with np.errstate(invalid="ignore"):
        differs = dissimilarity >= thresholds.dissimilarity_v
        differs_soft = dissimilarity >= thresholds.dissimilarity_v_soft

    with np.errstate(invalid="ignore"):
        strong = dissimilarity >= thresholds.dissimilarity_v_strong
    # El ancho solo no alcanza: medido sobre electrodos secos oscila 20-60 ms
    # latido a latido, y en las capturas del chaleco daba "QRS de 170 ms" en
    # latidos con la misma forma que la dominante (disimilitud 0,00-0,03). Un
    # QRS ancho necesita además una forma que se aparte de la dominante.
    ventricular = classifiable & (
        (wide_for_v & dominant_narrow & differs_soft)
        | strong
        | (differs & (premature | wide))
        | (premature & compensatory & differs_soft)
    )
    # S pide QRS angosto. Un prematuro ancho con la forma de la dominante (una
    # conducción aberrante, o un ancho mal medido) no es S ni alcanza para V:
    # queda Q, fuera de los conteos.
    supraventricular = classifiable & premature & ~wide & ~ventricular
    aberrant = classifiable & premature & wide & ~ventricular
    labels = np.full(n, bl.CODE_Q, dtype=np.uint8)
    labels[classifiable & ~aberrant] = bl.CODE_N
    labels[supraventricular] = bl.CODE_S
    labels[ventricular] = bl.CODE_V
    labels[~good] = bl.CODE_X

    evidence = (
        np.where(premature & classifiable, bl.EVIDENCE_PREMATURE, 0)
        | np.where(wide & classifiable, bl.EVIDENCE_WIDE, 0)
        | np.where(differs & classifiable, bl.EVIDENCE_MORPH_DIFFERS, 0)
        | np.where(compensatory & classifiable, bl.EVIDENCE_COMPENSATORY, 0)
    ).astype(np.uint8)

    contiguous = rr.valid
    scores = np.where(classifiable, morphology_scores, np.float32(np.nan))
    findings = ectopy_findings(
        rpeaks=rpeaks,
        labels=labels,
        contiguous=contiguous,
        owned=owned,
        ventricular_scores=scores,
        prematurity=prematurity,
        firmware_peaks=firmware_peaks,
        firmware_tolerance=firmware_tolerance,
        sample_rate=sample_rate,
        budget=budget,
        classifier=classifier,
    )

    # La conducción razona sobre los QRS: sin las detecciones que no lo son (una
    # P tomada por R partiría en dos el R-R de un latido bloqueado).
    keep = ~not_a_qrs
    if keep.all():
        view_peaks, view_rr, view_expected = rpeaks, rr, expected_rr
    else:
        view_peaks = rpeaks[keep]
        view_rr = build_rr(view_peaks, analyzable, sample_rate)
        view_expected = expected_rr_series(view_rr)
    view_normal = (labels == bl.CODE_N)[keep]
    view_qrs = qrs.select(keep)
    # La P se busca en los N que siguen a un N: después de un ectópico la
    # repolarización anterior tapa la P, y los S/V no se miden por PR.
    candidates = view_normal.copy()
    candidates[1:] &= view_normal[:-1]
    candidates[:1] = False
    # La respuesta del filtro de P es lo más caro del módulo: solo si hay latidos
    # normales donde buscar la P (y para las reglas de conducción que la leen).
    has_normal = int(view_normal.sum()) >= MIN_NORMAL_FOR_CONDUCTION
    response = p_filter(cleaned, sample_rate) if has_normal else np.empty(0, dtype=np.float32)
    p_view = (
        measure_p_waves(cleaned, view_peaks, view_qrs, candidates, sample_rate, response)
        if has_normal
        else empty_p_waves(view_peaks.size)
    )
    if has_normal:
        findings.extend(
            conduction_findings(
                ConductionInput(
                    rpeaks=view_peaks,
                    is_normal=view_normal,
                    qrs=view_qrs,
                    p=p_view,
                    contiguous=view_rr.valid,
                    expected_rr=view_expected,
                    dominant=dominant[keep],
                    owned=owned[keep],
                    cleaned=cleaned,
                    response=response,
                    analyzable=analyzable,
                    origin=origin,
                    sample_rate=sample_rate,
                    classifier=classifier,
                )
            )
        )
    # La P por latido del bloque entero, para el registro persistido.
    p_onset = np.zeros(n, dtype=np.int64)
    p_detected = np.zeros(n, dtype=bool)
    p_onset[keep] = p_view.onset
    p_detected[keep] = p_view.valid

    records = bl.build_records(
        rpeaks[owned],
        labels[owned],
        evidence[owned],
        qrs.onset[owned],
        qrs.offset[owned],
        p_onset[owned],
        qrs.amplitude_mv[owned],
        qrs_measured=qrs.valid[owned],
        p_detected=p_detected[owned],
    )
    return BeatClassification(labels=labels, records=records, findings=tuple(findings))


def _reference_amplitude(qrs: QrsDelineation, dominant: Mask, minimum: int) -> float:
    """Amplitud típica del QRS de la dominante; NaN (nada es "chico") si hay pocos latidos."""
    amplitudes = qrs.amplitude_mv[dominant & qrs.valid]
    return float(np.median(amplitudes)) if amplitudes.size >= minimum else float("nan")


def _quiet_enough(qrs: QrsDelineation, max_ratio: float) -> Mask:
    """Verdadero donde el ruido local es una fracción chica de la amplitud del QRS."""
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = qrs.noise_mv / qrs.amplitude_mv
    return np.asarray(np.isfinite(ratio) & (ratio <= max_ratio), dtype=bool)
