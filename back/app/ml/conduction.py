"""Onda P e intervalo PR por latido, y los hallazgos de conducción.

Con una sola derivación y electrodos secos la onda P es lo más frágil que se
mide: 0,1 mV a 0,2 mV de amplitud, del tamaño del ruido de un chaleco mal
puesto. La regla de este módulo es **no informar lo que no se vio**: un latido
sin una P clara queda sin PR (nunca un PR inventado), y los hallazgos de
bloqueo exigen que la P se haya detectado de forma consistente en el bloque
(`PR_MIN_BEATS`, `PR_MAX_SPREAD_MS`). Donde una derivación no alcanza para el
diagnóstico, el hallazgo se llama "posible ...".

**Detección de la P.** Un filtro de diferencia de gaussianas (`P_SIGMA_MS`)
sobre la señal limpia resalta lo que tiene el ancho de una P. Por latido se
busca el máximo del filtro en la ventana entre el fin de la T del latido
anterior (`QT` de Bazett sobre el R-R previo) y `P_QRS_GAP_MS` antes del inicio
del QRS; el máximo tiene que superar `P_MIN_SNR` veces el ruido de la ventana y
no estar pegado a sus bordes (una T que baja o un QRS que sube dan máximos en el
borde, no ondas P). El inicio de la P es donde la pendiente de la onda cae por
debajo de una fracción de su máximo, la misma regla que el QRS
(`qrs.bridged_extent`). PR = inicio de la P → inicio del QRS, que es la
definición del PR del cardiólogo (AHA/ACC/HRS 2009).

Una P que sigue a un latido que no es normal, o a un R-R de menos de
`MIN_RR_FOR_P_S`, no se busca: a más de ~100 lpm la P cae sobre la T anterior y
cualquier máximo ahí es una T.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml.contracts import Finding, Floats, Indices, Mask, Signal
from app.ml.qrs import QRS_WIDTH_VALIDATED, WIDE_QRS_MS, QrsDelineation

#: Ancho (sigma) del filtro de la P: una P normal dura ~100 ms (sigma ~ 20-25 ms).
#: El filtro es la diferencia entre esa gaussiana y otra `P_SIGMA_RATIO` veces
#: más ancha, que quita la línea de base y la rampa de la T.
P_SIGMA_MS: Final = 20.0
P_SIGMA_RATIO: Final = 3.0

#: Ventana de búsqueda de la P: termina `P_QRS_GAP_MS` antes del inicio del QRS
#: y mide `P_WINDOW_MS`. Con el PR máximo que se informa (`MAX_PR_MS`) el pico
#: de la P cae adentro con holgura.
P_QRS_GAP_MS: Final = 30.0
P_WINDOW_MS: Final = 340.0

#: La P se busca después del fin de la T anterior: `K * sqrt(R-R previo)` (Bazett
#: despejado, K = 0,4 s^1/2) más `P_AFTER_T_MARGIN_MS`.
QT_BAZETT_K: Final = 0.40
P_AFTER_T_MARGIN_MS: Final = 20.0

#: Por debajo de este R-R previo, la P se confunde con la T anterior: no se busca.
MIN_RR_FOR_P_S: Final = 0.60

#: El máximo del filtro tiene que superar este múltiplo del ruido (MAD de la
#: ventana) y este piso absoluto en mV.
P_MIN_SNR: Final = 4.0
P_MIN_RESPONSE_MV: Final = 0.015

#: Un máximo a menos de esto del borde de la ventana no es una onda: es la
#: rampa de la T o del QRS cortada por la ventana.
P_EDGE_MARGIN_MS: Final = 16.0

#: Búsqueda del inicio: hasta dónde se mira a la izquierda del pico, qué
#: fracción de la altura de la respuesta marca el pie de la onda y cuánto se
#: corrige. Calibrados contra las marcas de P de la QT Database (`q1c`), ver
#: `docs/deteccion-ml/README.md`.
P_ONSET_WINDOW_MS: Final = 140.0
P_ONSET_FRACTION: Final = 0.20
P_ONSET_SHIFT_MS: Final = 20.0

#: Rango de PR que se acepta como medición. Fuera de esto no es un PR: es otra
#: onda, o una P que pertenece a otro latido.
MIN_PR_MS: Final = 40.0
MAX_PR_MS: Final = 400.0

#: Sin evidencia de la P: valor de `p_onset` en el formato persistido.
NO_P: Final = np.iinfo(np.int16).min


@dataclass(frozen=True, slots=True)
class PWaves:
    """Por R del tren de entrada. Las muestras son **relativas al R**."""

    #: Inicio de la P (negativo). Sin sentido donde `valid` es falso.
    onset: Indices
    #: Pico de la P (negativo).
    peak: Indices
    valid: Mask
    #: Altura de la respuesta del filtro en el pico, en mV; NaN donde no hay P.
    #: Es la medida de cuán clara es la onda: el buscador de ondas bloqueadas
    #: la compara con la de las P conducidas del propio paciente.
    height: Floats

    def pr_ms(self, qrs: QrsDelineation, sample_rate: int) -> Floats:
        """PR en ms (inicio de P a inicio de QRS); NaN donde no hay P o QRS."""
        pr = (qrs.onset - self.onset).astype(np.float32) * (1000.0 / sample_rate)
        return np.where(self.valid & qrs.valid, pr, np.float32(np.nan)).astype(np.float32)


def empty_p_waves(n: int) -> PWaves:
    return PWaves(
        onset=np.zeros(n, dtype=np.int64),
        peak=np.zeros(n, dtype=np.int64),
        valid=np.zeros(n, dtype=bool),
        height=np.full(n, np.nan, dtype=np.float32),
    )


def p_filter(cleaned: Signal, sample_rate: int) -> Signal:
    """Diferencia de gaussianas centrada en el ancho de una onda P."""
    sigma = P_SIGMA_MS * sample_rate / 1000.0
    data = np.asarray(cleaned, dtype=np.float32)
    return np.asarray(
        gaussian_filter1d(data, sigma, mode="nearest")
        - gaussian_filter1d(data, sigma * P_SIGMA_RATIO, mode="nearest"),
        dtype=np.float32,
    )


def _white_noise_gain(sample_rate: int) -> float:
    """Desvío de la salida de `p_filter` por unidad de desvío de ruido blanco."""
    sigma = P_SIGMA_MS * sample_rate / 1000.0
    wide = sigma * P_SIGMA_RATIO
    root_pi = np.sqrt(np.pi)
    energy = (
        1.0 / (2.0 * root_pi * sigma)
        + 1.0 / (2.0 * root_pi * wide)
        - 2.0 / np.sqrt(2.0 * np.pi * (sigma**2 + wide**2))
    )
    return float(np.sqrt(energy))


def measure_p_waves(
    cleaned: Signal,
    rpeaks: Indices,
    qrs: QrsDelineation,
    candidates: Mask,
    sample_rate: int,
    response: Signal | None = None,
) -> PWaves:
    """La P de cada latido candidato. **Puro y vectorizado.**

    `candidates` marca los latidos donde vale la pena buscarla (los que el
    clasificador llamó N, con un N antes). Se saltean los de R-R previo corto y
    los que no tienen QRS medido: sin el inicio del QRS no hay ventana ni PR.
    `response` es la salida de `p_filter(cleaned)` si el llamador ya la tiene:
    filtrar el bloque entero es lo más caro de este módulo y se hace una vez.
    """
    n = int(rpeaks.size)
    result = empty_p_waves(n)
    if n < 2:
        return result

    previous_rr = np.full(n, np.nan, dtype=np.float64)
    previous_rr[1:] = np.diff(rpeaks) / float(sample_rate)
    window = int(round(P_WINDOW_MS * sample_rate / 1000))
    gap = int(round(P_QRS_GAP_MS * sample_rate / 1000))
    wanted = candidates & qrs.valid & np.isfinite(previous_rr) & (previous_rr >= MIN_RR_FOR_P_S)
    end = rpeaks + qrs.onset - gap
    wanted &= (end - window + 1 >= 0) & (end < cleaned.size)
    rows = np.flatnonzero(wanted)
    if rows.size == 0:
        return result

    if response is None:
        response = p_filter(cleaned, sample_rate)
    columns = end[rows, None] - (window - 1) + np.arange(window)[None, :]
    values = response[columns]
    # Lo anterior al fin de la T previa no se mira: ahí el máximo es la T.
    t_end = rpeaks[rows - 1] + np.round(
        (QT_BAZETT_K * np.sqrt(previous_rr[rows]) + P_AFTER_T_MARGIN_MS / 1000.0) * sample_rate
    ).astype(np.int64)
    usable = columns >= t_end[:, None]
    edge = max(int(round(P_EDGE_MARGIN_MS * sample_rate / 1000)), 1)
    first_usable = np.argmax(usable, axis=1)
    enough = usable.any(axis=1) & (window - first_usable > 4 * edge)

    masked = np.where(usable, values, -np.inf)
    peak_column = np.argmax(masked, axis=1)
    peak_value = masked[np.arange(rows.size), peak_column]
    # Ruido: el de muestra a muestra de la señal en la parte usable de la
    # ventana (MAD de la primera diferencia), llevado a la escala de la
    # respuesta del filtro. La MAD de la respuesta misma no sirve: la P es una
    # fracción grande de la ventana y se mide contra sí misma.
    samples = cleaned[columns]
    steps = np.where(usable[:, 1:] & usable[:, :-1], np.abs(np.diff(samples, axis=1)), np.nan)
    with np.errstate(all="ignore"):
        white_sigma = np.nanmedian(steps, axis=1) * 1.4826 / np.sqrt(2.0)
    noise = white_sigma * _white_noise_gain(sample_rate)
    interior = (peak_column >= first_usable + edge) & (peak_column <= window - 1 - edge)
    clear = (
        enough
        & interior
        & np.isfinite(peak_value)
        & (peak_value >= P_MIN_RESPONSE_MV)
        & (peak_value >= P_MIN_SNR * np.nan_to_num(noise, nan=np.inf))
    )

    peak_abs = columns[np.arange(rows.size), peak_column]
    onset_abs = _p_onsets(response, peak_abs, peak_value, sample_rate)
    onset_rel = onset_abs - rpeaks[rows]
    pr_ms = (qrs.onset[rows] - onset_rel) * (1000.0 / sample_rate)
    good = clear & (onset_abs > 0) & (pr_ms >= MIN_PR_MS) & (pr_ms <= MAX_PR_MS)
    result.onset[rows] = onset_rel
    result.peak[rows] = peak_abs - rpeaks[rows]
    result.valid[rows] = good
    result.height[rows] = np.where(good, peak_value, np.float32(np.nan))
    return result


def _p_onsets(response: Signal, peaks: Indices, heights: Floats, sample_rate: int) -> Indices:
    """Inicio de cada P: donde la respuesta del filtro baja de una fracción de su pico.

    Se camina hacia la izquierda desde el pico hasta el primer punto bajo
    `P_ONSET_FRACTION` de la altura; -1 donde no baja en `P_ONSET_WINDOW_MS`
    (la P se confunde con la onda anterior). La respuesta es una campana suave,
    sin los quiebres de la pendiente de una P ancha y chata, y a la derecha de
    la T anterior el máximo ya está acotado por la ventana de búsqueda.
    `P_ONSET_SHIFT_MS` corrige la demora que le suma el suavizado del filtro.
    """
    reach = int(round(P_ONSET_WINDOW_MS * sample_rate / 1000))
    out = np.full(peaks.size, -1, dtype=np.int64)
    rows = np.flatnonzero(peaks >= reach)
    if rows.size == 0:
        return out
    # Columnas 0..reach hacia atrás desde el pico: la 0 es el pico mismo.
    columns = peaks[rows, None] - np.arange(reach + 1)[None, :]
    below = response[columns] <= (P_ONSET_FRACTION * heights[rows])[:, None]
    found = below.any(axis=1)
    steps = np.argmax(below, axis=1)
    shift = int(round(P_ONSET_SHIFT_MS * sample_rate / 1000))
    out[rows] = np.where(found, peaks[rows] - steps - shift, -1)
    return out


# --------------------------------------------------------------------------- #
# Hallazgos de conducción
# --------------------------------------------------------------------------- #

KIND_FIRST_DEGREE = "av_block_first_degree"
KIND_MOBITZ_1 = "av_block_mobitz_1"
KIND_MOBITZ_2 = "av_block_mobitz_2"
KIND_COMPLETE = "av_block_complete"
KIND_WIDE_QRS = "wide_qrs_sustained"
KIND_SHORT_PR = "short_pr"
CONDUCTION_KINDS: Final = frozenset(
    {KIND_FIRST_DEGREE, KIND_MOBITZ_1, KIND_MOBITZ_2, KIND_COMPLETE, KIND_WIDE_QRS, KIND_SHORT_PR}
)

#: Sostenido: lo que se mide por bloques de esta duración alineados al estudio
#: (30 s, la convención de los hallazgos de ritmo: `ml_rhythm_min_seconds`).
BIN_SECONDS: Final = 30.0

#: PR largo (BAV de 1.er grado) y corto, en ms (AHA/ACC/HRS 2009: normal 120-200).
LONG_PR_MS: Final = 200.0
SHORT_PR_MS: Final = 120.0

#: Para informar un PR de un bloque de 30 s hacen falta estos latidos con P
#: clara, repartidos en al menos `PR_MIN_SPAN_S`, y que su PR sea parejo: una P
#: detectada a medias da PR al azar, y el PR al azar no tiene mediana que valga.
PR_MIN_BEATS: Final = 10
PR_MIN_SPAN_S: Final = 15.0
PR_MAX_SPREAD_MS: Final = 20.0

#: Ancho de QRS sostenido: latidos de la morfología dominante, medianas de 30 s.
WIDE_MIN_BEATS: Final = 10
WIDE_MAX_SPREAD_MS: Final = 20.0

#: Margen del hallazgo alrededor de sus latidos extremos.
FINDING_MARGIN_MS: Final = 100

#: P no conducida (BAV de 2.º grado): R-R entre estas razones del esperado.
#: Menos de 1,7 no es un latido caído; más de 2,6 son varios o una pausa, que
#: ya tiene su propia regla.
BLOCKED_RR_RATIO_MIN: Final = 1.7
BLOCKED_RR_RATIO_MAX: Final = 2.6
#: La P bloqueada se busca en torno a donde la predice el P-P del paciente:
#: esta fracción del R-R esperado a cada lado (piso en ms).
BLOCKED_SEARCH_FRACTION: Final = 0.15
BLOCKED_SEARCH_MIN_MS: Final = 80.0
#: Altura relativa a la mediana de las P conducidas: por debajo no es la P del
#: paciente; por encima es un QRS que el detector no vio.
BLOCKED_MIN_HEIGHT_RATIO: Final = 0.5
BLOCKED_MAX_HEIGHT_RATIO: Final = 2.5
#: Cantidad de latidos con PR válido antes del bloqueo para clasificarlo, y los
#: umbrales de la clasificación: Mobitz I alarga el PR de a poco y el latido
#: que sigue lo acorta; Mobitz II lo deja constante.
MOBITZ_MIN_PRIOR: Final = 3
MOBITZ_PRIOR_BEATS: Final = 5
MOBITZ_1_MIN_GROWTH_MS: Final = 20.0
MOBITZ_1_NOT_SHRINKING_MS: Final = 8.0
MOBITZ_1_RECOVERY_MS: Final = 15.0
MOBITZ_2_MAX_RANGE_MS: Final = 20.0
MOBITZ_2_MAX_JUMP_MS: Final = 25.0
#: Guarda de P para el push de Mobitz II y BAV completo: PR válidos previos
#: y claridad relativa de la P bloqueada.
PUSH_MIN_PRIOR: Final = 4
PUSH_MIN_HEIGHT_RATIO: Final = 0.7

#: Posible BAV completo: FC ventricular, regularidad R-R, cuántas P visibles hacen
#: falta en 30 s (las que caen sobre un QRS o una T no se ven), qué tan
#: concentradas en un período común tienen que estar (`_period`), y cuánta
#: concentración de los QRS contra ese período se tolera (más es conducción).
COMPLETE_MAX_BPM: Final = 50.0
COMPLETE_MIN_BEATS: Final = 8
COMPLETE_MAX_RR_SPREAD: Final = 0.10
COMPLETE_MIN_P: Final = 8
COMPLETE_MIN_PP_S: Final = 0.40
COMPLETE_MAX_PP_S: Final = 1.20
COMPLETE_MIN_P_LOCK: Final = 0.85
COMPLETE_MAX_QRS_LOCK: Final = 0.60
COMPLETE_RATE_RATIO: Final = 0.70
COMPLETE_PUSH_MIN_P: Final = 14
COMPLETE_PUSH_P_LOCK: Final = 0.93
#: Las P se buscan fuera del QRS (desde esto antes del R) y de la T (hasta
#: `COMPLETE_T_FACTOR` veces el QT de Bazett después).
COMPLETE_QRS_GUARD_MS: Final = 80.0
COMPLETE_T_FACTOR: Final = 1.15

ALERT_BLOCK = "Se detectó un posible bloqueo auriventricular."


@dataclass(frozen=True, slots=True)
class ConductionInput:
    """Lo que las reglas de conducción leen del bloque. Todo por R, mismo largo."""

    rpeaks: Indices
    is_normal: Mask
    qrs: QrsDelineation
    p: PWaves
    #: Verdadero si el R-R entre `i` e `i+1` es válido (largo `n - 1`).
    contiguous: Mask
    #: R-R esperado (`hrv.expected_rr`), segundos; NaN el primero.
    expected_rr: Floats
    #: Latidos de la morfología dominante.
    dominant: Mask
    owned: Mask
    cleaned: Signal
    #: `p_filter(cleaned)`, calculada una vez para todo el bloque.
    response: Signal
    analyzable: Mask
    #: Muestra absoluta del primer elemento de las señales: alinea los bloques
    #: de 30 s al estudio entero, no a cada lote.
    origin: int
    sample_rate: int
    classifier: str


def conduction_findings(data: ConductionInput) -> list[Finding]:
    """Todos los hallazgos de conducción del bloque. Coordenadas **relativas al lote**."""
    if data.rpeaks.size < 3:
        return []
    pr = data.p.pr_ms(data.qrs, data.sample_rate)
    findings = _pr_findings(data, pr)
    if QRS_WIDTH_VALIDATED:
        findings.extend(_wide_qrs_findings(data))
    response = data.response
    noise = _global_noise(data)
    findings.extend(_blocked_p_findings(data, pr, response, noise))
    findings.extend(_complete_block_findings(data, response, noise))
    return findings


def _bins(data: ConductionInput) -> Indices:
    """Número de bloque de 30 s (alineado al estudio) de cada R."""
    size = int(BIN_SECONDS * data.sample_rate)
    return (data.rpeaks + data.origin) // size


def _bin_runs(flagged: list[int]) -> list[list[int]]:
    """Agrupa números de bloque consecutivos."""
    runs: list[list[int]] = []
    for value in sorted(flagged):
        if runs and value == runs[-1][-1] + 1:
            runs[-1].append(value)
        else:
            runs.append([value])
    return runs


def _spread(values: np.ndarray) -> float:
    """Desvío robusto (MAD escalada) de `values`, en sus unidades."""
    return float(1.4826 * np.median(np.abs(values - np.median(values))))


def _finding(
    data: ConductionInput,
    kind: str,
    severity: ECGEventSeverity,
    beats: Indices,
    *,
    metadata: dict[str, float | int | str],
    alert: str | None = None,
    push: bool = True,
    score: float | None = None,
) -> Finding:
    margin = int(FINDING_MARGIN_MS * data.sample_rate / 1000)
    start = max(int(beats[0]) - margin, 0)
    length = max(int(beats[-1]) - int(beats[0]) + 2 * margin, 1)
    return Finding(
        kind=kind,
        event_type=ECGEventType.CONDUCTION,
        severity=severity,
        start_sample=start,
        length_samples=length,
        dedupe_key=f"{kind}:{start}",
        score=score,
        beat_count=int(beats.size),
        alert_message=alert,
        push=push,
        beat_samples=tuple(int(sample) for sample in beats),
        metadata={
            **metadata,
            "durationSeconds": round(length / data.sample_rate, 2),
            "classifier": data.classifier,
        },
    )


def _pr_findings(data: ConductionInput, pr: Floats) -> list[Finding]:
    """BAV de 1.er grado (PR largo) y PR corto, por mediana de 30 s.

    Solo latidos normales con un PR medido: si la P no se detectó con confianza,
    no hay PR y el latido no vota. Un bloque necesita `PR_MIN_BEATS` de ellos
    repartidos en `PR_MIN_SPAN_S` y con un PR parejo (`PR_MAX_SPREAD_MS`): sin
    eso la P que se ve no es la P de este paciente.
    """
    usable = data.is_normal & np.isfinite(pr)
    bins = _bins(data)
    long_bins: dict[int, Indices] = {}
    short_bins: dict[int, Indices] = {}
    medians: dict[int, float] = {}
    for number in np.unique(bins[usable]):
        members = np.flatnonzero(usable & (bins == number))
        span = (data.rpeaks[members[-1]] - data.rpeaks[members[0]]) / data.sample_rate
        if members.size < PR_MIN_BEATS or span < PR_MIN_SPAN_S:
            continue
        if _spread(pr[members]) > PR_MAX_SPREAD_MS:
            continue
        median = float(np.median(pr[members]))
        medians[int(number)] = median
        if median > LONG_PR_MS:
            long_bins[int(number)] = members
        elif median < SHORT_PR_MS:
            short_bins[int(number)] = members

    findings: list[Finding] = []
    for kind, found in ((KIND_FIRST_DEGREE, long_bins), (KIND_SHORT_PR, short_bins)):
        for run in _bin_runs(list(found)):
            members = np.concatenate([found[number] for number in run])
            if not data.owned[members].any():
                continue
            findings.append(
                _finding(
                    data,
                    kind,
                    ECGEventSeverity.LOW,
                    data.rpeaks[members],
                    metadata={
                        "prMs": round(float(np.median(pr[members])), 1),
                        "prSpreadMs": round(_spread(pr[members]), 1),
                    },
                )
            )
    return findings


def _wide_qrs_findings(data: ConductionInput) -> list[Finding]:
    """QRS ancho sostenido de la morfología dominante: "posible bloqueo de rama".

    Con una derivación no se distingue rama izquierda de derecha, y por eso el
    nombre. Solo los latidos de la dominante cuentan: un ectópico ancho aislado
    no es un bloqueo.
    """
    width = data.qrs.width_ms(data.sample_rate)
    usable = data.dominant & np.isfinite(width)
    bins = _bins(data)
    wide: dict[int, Indices] = {}
    for number in np.unique(bins[usable]):
        members = np.flatnonzero(usable & (bins == number))
        if members.size < WIDE_MIN_BEATS or _spread(width[members]) > WIDE_MAX_SPREAD_MS:
            continue
        if float(np.median(width[members])) >= WIDE_QRS_MS:
            wide[int(number)] = members
    findings: list[Finding] = []
    for run in _bin_runs(list(wide)):
        members = np.concatenate([wide[number] for number in run])
        if not data.owned[members].any():
            continue
        findings.append(
            _finding(
                data,
                KIND_WIDE_QRS,
                ECGEventSeverity.MEDIUM,
                data.rpeaks[members],
                metadata={"qrsMs": round(float(np.median(width[members])), 1)},
            )
        )
    return findings


def _global_noise(data: ConductionInput) -> float:
    """Ruido de la respuesta del filtro de P, medido en toda la señal analizable del bloque."""
    steps = np.abs(np.diff(data.cleaned.astype(np.float64)))
    good = data.analyzable[1:] & data.analyzable[:-1]
    if not good.any():
        return float("inf")
    sigma = float(np.median(steps[good])) * 1.4826 / np.sqrt(2.0)
    return float(sigma * _white_noise_gain(data.sample_rate))


def _blocked_p_findings(
    data: ConductionInput, pr: Floats, response: Signal, noise: float
) -> list[Finding]:
    """BAV de 2.º grado: una P donde el R-R largo perdió el QRS.

    Candidatos: R-R de 1,7 a 2,6 veces el esperado entre dos latidos normales
    angostos con una P conducida detrás. La P bloqueada se busca donde la
    predice el P-P de este paciente; tiene que parecerse a sus P conducidas.
    Mobitz I si el PR de los latidos previos se alarga y el siguiente lo acorta;
    Mobitz II si el PR es constante. Cualquier otra cosa no se informa.
    """
    n = data.rpeaks.size
    rate = data.sample_rate
    rr = np.diff(data.rpeaks) / float(rate)
    expected = data.expected_rr[1:]
    width = data.qrs.width_ms(rate)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = rr / expected
    candidates = np.flatnonzero(
        (ratio >= BLOCKED_RR_RATIO_MIN)
        & (ratio <= BLOCKED_RR_RATIO_MAX)
        & (expected >= MIN_RR_FOR_P_S)
        & data.contiguous
        & data.is_normal[:-1]
        & data.is_normal[1:]
        & data.p.valid[:-1]
        & (width[:-1] < WIDE_QRS_MS)
    )
    if candidates.size == 0:
        return []
    typical_height = float(np.nanmedian(data.p.height)) if np.isfinite(data.p.height).any() else 0.0
    if typical_height <= 0 or not np.isfinite(noise):
        return []

    findings: list[Finding] = []
    for candidate in candidates:
        i = int(candidate)
        if i + 1 >= n or not np.isfinite(pr[i + 1]) or not data.owned[i : i + 2].any():
            continue
        height = _blocked_p_height(data, response, i, float(expected[i]), noise)
        if height is None:
            continue
        ratio_to_typical = height / typical_height
        if not BLOCKED_MIN_HEIGHT_RATIO <= ratio_to_typical <= BLOCKED_MAX_HEIGHT_RATIO:
            continue
        prior = _prior_pr(data, pr, i, ratio)
        if prior.size < MOBITZ_MIN_PRIOR:
            continue
        after = float(pr[i + 1])
        if (
            np.all(np.diff(prior) >= -MOBITZ_1_NOT_SHRINKING_MS)
            and prior[-1] - prior[0] >= MOBITZ_1_MIN_GROWTH_MS
            and after <= prior[-1] - MOBITZ_1_RECOVERY_MS
        ):
            kind, severity = KIND_MOBITZ_1, ECGEventSeverity.MEDIUM
        elif (
            float(prior.max() - prior.min()) <= MOBITZ_2_MAX_RANGE_MS
            and abs(after - float(np.median(prior))) <= MOBITZ_2_MAX_JUMP_MS
        ):
            kind, severity = KIND_MOBITZ_2, ECGEventSeverity.HIGH
        else:
            continue
        guarded = prior.size >= PUSH_MIN_PRIOR and ratio_to_typical >= PUSH_MIN_HEIGHT_RATIO
        findings.append(
            _finding(
                data,
                kind,
                severity,
                data.rpeaks[i : i + 2],
                metadata={
                    "priorPrMs": round(float(np.median(prior)), 1),
                    "nextPrMs": round(after, 1),
                    "priorBeats": int(prior.size),
                    "blockedPRatio": round(ratio_to_typical, 2),
                },
                alert=ALERT_BLOCK if severity is ECGEventSeverity.HIGH else None,
                push=guarded,
            )
        )
    return findings


def _prior_pr(data: ConductionInput, pr: Floats, index: int, rr_ratio: np.ndarray) -> Floats:
    """PR de los latidos conducidos consecutivos que terminan en `index`, en orden.

    Se corta en un R-R largo (un latido caído anterior): la serie de un
    ciclo de Wenckebach arranca después del bloqueo anterior, no antes.
    `rr_ratio[j]` es el R-R de `j` a `j+1` sobre el esperado.
    """
    low = max(index - MOBITZ_PRIOR_BEATS + 1, 0)
    values: list[float] = []
    for j in range(index, low - 1, -1):
        if not np.isfinite(pr[j]):
            break
        if j < index and (not data.contiguous[j] or rr_ratio[j] >= BLOCKED_RR_RATIO_MIN):
            break
        values.append(float(pr[j]))
    return np.asarray(values[::-1], dtype=np.float64)


def _blocked_p_height(
    data: ConductionInput, response: Signal, index: int, expected_s: float, noise: float
) -> float | None:
    """Altura de la P bloqueada del R-R que sigue al latido `index`, o None si no hay."""
    rate = data.sample_rate
    centre = int(data.rpeaks[index] + data.p.peak[index] + round(expected_s * rate))
    half = max(
        round(BLOCKED_SEARCH_FRACTION * expected_s * rate),
        round(BLOCKED_SEARCH_MIN_MS * rate / 1000),
    )
    previous_rr = float(data.rpeaks[index] - data.rpeaks[index - 1]) / rate if index else expected_s
    after_t = int(
        data.rpeaks[index]
        + round((QT_BAZETT_K * np.sqrt(previous_rr) + P_AFTER_T_MARGIN_MS / 1000.0) * rate)
    )
    low, high = max(centre - half, after_t), min(centre + half, int(data.rpeaks[index + 1]) - 1)
    edge = max(int(round(P_EDGE_MARGIN_MS * rate / 1000)), 1)
    if high - low < 4 * edge or low < 0 or high >= response.size:
        return None
    window = response[low : high + 1]
    peak = int(np.argmax(window))
    height = float(window[peak])
    if peak < edge or peak > window.size - 1 - edge:
        return None
    if height < P_MIN_RESPONSE_MV or height < P_MIN_SNR * noise:
        return None
    return height


def _complete_block_findings(
    data: ConductionInput, response: Signal, noise: float
) -> list[Finding]:
    """ "Posible BAV completo": ondas P regulares e independientes de un QRS lento y regular.

    Por bloque de 30 s: FC ventricular menor de 50 lpm con R-R regulares, y
    entre los QRS (fuera de cada QRS y de su T) una cadena de P con un período
    común (`_period`) más rápida que los QRS y **sin relación de fase con
    ellos**. La fase importa: en un bloqueo 2:1 la frecuencia ventricular es la
    mitad de la auricular pero cada QRS cae siempre en la misma fase de las P,
    y eso no es una disociación. Es lo más exigente que se pide a la P, y por eso
    el nombre es "posible" y el push exige una cadena larga y muy regular.
    """
    if not np.isfinite(noise):
        return []
    rate = data.sample_rate
    bins = _bins(data)
    size = int(BIN_SECONDS * rate)
    found: list[Finding] = []
    for number in np.unique(bins):
        members = np.flatnonzero(bins == number)
        if members.size < COMPLETE_MIN_BEATS or not data.owned[members].any():
            continue
        if not data.contiguous[members[:-1]].all():
            continue
        rr = np.diff(data.rpeaks[members]) / float(rate)
        median_rr = float(np.median(rr))
        if 60.0 / median_rr >= COMPLETE_MAX_BPM or _spread(rr) / median_rr > COMPLETE_MAX_RR_SPREAD:
            continue
        low = int(number) * size - data.origin
        waves = _free_p_waves(
            data, response, noise, max(low, 0), min(low + size, response.size), median_rr
        )
        if waves.size < COMPLETE_MIN_P:
            continue
        period, atrial_lock = _period(waves / float(rate))
        if atrial_lock < COMPLETE_MIN_P_LOCK or period > COMPLETE_RATE_RATIO * median_rr:
            continue
        beats = data.rpeaks[members]
        if _lock(beats / float(rate), period) > COMPLETE_MAX_QRS_LOCK:
            continue
        guarded = waves.size >= COMPLETE_PUSH_MIN_P and atrial_lock >= COMPLETE_PUSH_P_LOCK
        found.append(
            _finding(
                data,
                KIND_COMPLETE,
                ECGEventSeverity.CRITICAL,
                beats,
                metadata={
                    "ventricularBpm": round(60.0 / median_rr, 1),
                    "atrialBpm": round(60.0 / period, 1),
                    "pWaves": int(waves.size),
                    "pLock": round(atrial_lock, 3),
                },
                alert=ALERT_BLOCK,
                push=guarded,
            )
        )
    return found


def _lock(times_s: np.ndarray, period_s: float) -> float:
    """Concentración de fase de `times_s` módulo `period_s` (0 = repartida, 1 = clavada)."""
    angles = 2.0 * np.pi * times_s / period_s
    return float(np.hypot(np.cos(angles).mean(), np.sin(angles).mean()))


def _period(times_s: np.ndarray) -> tuple[float, float]:
    """El período que mejor ordena `times_s` y su concentración de fase.

    Busca entre `COMPLETE_MIN_PP_S` y `COMPLETE_MAX_PP_S`, de a 1 ms, y se
    queda con el de mayor concentración de fase. Sirve aunque falten ondas (las
    que caen sobre un QRS o una T no se ven): la concentración mira dónde caen
    las que sí se ven, no los intervalos entre ellas.
    """
    candidates = np.arange(COMPLETE_MIN_PP_S, COMPLETE_MAX_PP_S, 0.001)
    angles = 2.0 * np.pi * times_s[:, None] / candidates[None, :]
    lock = np.hypot(np.cos(angles).mean(axis=0), np.sin(angles).mean(axis=0))
    best = int(np.argmax(lock))
    return float(candidates[best]), float(lock[best])


def _free_p_waves(
    data: ConductionInput,
    response: Signal,
    noise: float,
    low: int,
    high: int,
    median_rr: float,
) -> Indices:
    """Picos del filtro de P en `[low, high)` que no caen sobre un QRS ni sobre su T."""
    if high - low < 8:
        return np.empty(0, dtype=np.int64)
    rate = data.sample_rate
    height = max(P_MIN_RESPONSE_MV, P_MIN_SNR * noise)
    peaks, _ = find_peaks(response[low:high], height=height, distance=int(0.3 * rate))
    peaks = peaks + low
    guard = int(COMPLETE_QRS_GUARD_MS * rate / 1000)
    t_after = int(round(COMPLETE_T_FACTOR * QT_BAZETT_K * np.sqrt(median_rr) * rate))
    beats = data.rpeaks
    position = np.searchsorted(beats, peaks)
    previous = beats[np.clip(position - 1, 0, beats.size - 1)]
    following = beats[np.clip(position, 0, beats.size - 1)]
    # Cae dentro de la T del latido anterior (hasta `t_after`) o a menos de
    # `guard` del siguiente (su QRS y su subida).
    in_t = (peaks >= previous) & (peaks - previous <= t_after)
    in_qrs = (following >= peaks) & (following - peaks <= guard)
    return np.asarray(peaks[~in_t & ~in_qrs], dtype=np.int64)
