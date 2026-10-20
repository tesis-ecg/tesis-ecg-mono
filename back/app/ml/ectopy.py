"""Patrones de ectopia (S y V) y los hallazgos que salen de ellos.

**La única definición de los patrones.** `ectopy_patterns` la usan el motor,
para armar sus hallazgos, y las métricas del informe Holter
(`holter_metrics.py`), para contar. Una segunda copia terminaría dando cifras
distintas de lo mismo según quién las pida.

Reglas (Requerimientos §4, convenciones Holter):

- **Aislada**: una S o V sin otra pegada. Incluye las que forman parte de un
  bigeminismo o un trigeminismo: ahí cada una está sola entre dos normales.
- **Par**: dos consecutivas. **Corrida**: tres o más consecutivas.
- **Bigeminismo**: tres o más ciclos seguidos `N, X`. **Trigeminismo**: tres o
  más ciclos seguidos `N, N, X`. `bigeminy_beats` y `trigeminy_beats` cuentan
  los latidos ectópicos (uno por ciclo).
- Un latido X (ruido) o Q (no clasificable) corta cualquier patrón, y también
  un tramo de señal que no es continuo (`contiguous`).

**Hallazgos.** Una corrida de tres o más a más de `RUN_MIN_BPM` es una "posible"
taquicardia supraventricular (S) o ventricular (V): `svt_run` y `vt_run`.
Sostenida es que dura `SUSTAINED_SECONDS` o más. El resto de las ectópicas se
agrupa en episodios (`supraventricular_ectopy`, `ventricular_ectopy`) de al
menos dos latidos; las aisladas solas no producen hallazgo y solo cuentan en
las métricas. Nada de esto afirma un diagnóstico: con una derivación y
electrodos secos, es lo que el dato respalda.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml.beat_labels import CODE_N, CODE_S, CODE_V
from app.ml.contracts import EpisodeBudget, Finding, Floats, Indices, Mask
from app.ml.morphology import BEAT_POST_MS, BEAT_PRE_MS

Target = Literal["S", "V"]

#: Una corrida de tres o más latidos S o V es taquicardia por encima de esta
#: frecuencia (AHA/ACC/HRS: taquicardia = más de 100 lpm; tres o más latidos
#: consecutivos).
RUN_MIN_BEATS: Final = 3
RUN_MIN_BPM: Final = 100.0

#: Sostenida: dura al menos esto (convención de Holter y de las guías de TV).
SUSTAINED_SECONDS: Final = 30.0

#: Ciclos seguidos que hacen falta para llamar a un patrón bigeminismo o
#: trigeminismo.
CYCLES_MIN: Final = 3

#: Margen del hallazgo de corrida alrededor de sus R. Con 0 la banda de tres
#: latidos terminaría sobre el pico del último.
RUN_MARGIN_MS: Final = 100

#: Un episodio de ectópicas con un par o con esta cantidad de latidos en
#: patrones sube a MEDIUM.
EPISODE_MIN_BEATS: Final = 2

#: Fracción de los R de una corrida ventricular no sostenida que tiene que
#: confirmar el detector del firmware para que mande push al paciente: la
#: taquicardia ventricular es lo que más falsos produce con el electrodo seco.
VT_PUSH_FIRMWARE_FRACTION: Final = 0.80

KIND_VENTRICULAR: Final = "ventricular_ectopy"
KIND_SUPRAVENTRICULAR: Final = "supraventricular_ectopy"
KIND_SVT_RUN: Final = "svt_run"
KIND_VT_RUN: Final = "vt_run"
RUN_KINDS: Final = frozenset({KIND_SVT_RUN, KIND_VT_RUN})

ALERT_VT = "Se detectó un posible episodio de taquicardia ventricular."
ALERT_SVT = "Se detectó un posible episodio sostenido de taquicardia supraventricular."

_SEVERITY_RANK = {
    ECGEventSeverity.LOW: 0,
    ECGEventSeverity.MEDIUM: 1,
    ECGEventSeverity.HIGH: 2,
    ECGEventSeverity.CRITICAL: 3,
}


@dataclass(frozen=True, slots=True)
class EctopyPatterns:
    """Conteos y máscaras por latido de un objetivo (`S` o `V`).

    Las máscaras (`*_mask`) tienen el largo de la serie y marcan los latidos
    objetivo de cada patrón. `runs` es `(k, 2)`: primer y último índice de cada
    corrida de tres o más; `bigeminy_spans` y `trigeminy_spans`, lo mismo para
    el patrón entero (de la `N` del primer ciclo a la ectópica del último).
    """

    total: int
    single: int
    pairs: int
    pair_beats: int
    runs: NDArray[np.int64]
    run_beats: int
    bigeminy_episodes: int
    bigeminy_beats: int
    trigeminy_episodes: int
    trigeminy_beats: int
    pair_mask: Mask
    run_mask: Mask
    bigeminy_mask: Mask
    trigeminy_mask: Mask
    bigeminy_spans: NDArray[np.int64]
    trigeminy_spans: NDArray[np.int64]


def ectopy_patterns(labels: NDArray[np.uint8], contiguous: Mask, target: Target) -> EctopyPatterns:
    """Patrones de `target` sobre la serie de etiquetas. **Puro y vectorizado.**

    `contiguous[i]` es verdadero si los latidos `i` e `i+1` están en el mismo
    tramo continuo de señal (la serie R-R entre ellos es válida). Acepta un
    arreglo de largo `n - 1` o `n`; solo se leen los primeros `n - 1`.
    """
    n = int(labels.size)
    code = CODE_S if target == "S" else CODE_V
    is_target = labels == code
    link = np.asarray(contiguous[: max(n - 1, 0)], dtype=bool)

    runs_start, runs_end = _runs(is_target, link)
    lengths = runs_end - runs_start + 1
    pair_mask = np.zeros(n, dtype=bool)
    run_mask = np.zeros(n, dtype=bool)
    for first, last in zip(runs_start[lengths == 2], runs_end[lengths == 2], strict=True):
        pair_mask[first : last + 1] = True
    long_runs = lengths >= RUN_MIN_BEATS
    for first, last in zip(runs_start[long_runs], runs_end[long_runs], strict=True):
        run_mask[first : last + 1] = True

    bigeminy_mask, bigeminy_spans = _cycles(labels, is_target, link, period=2)
    trigeminy_mask, trigeminy_spans = _cycles(labels, is_target, link, period=3)
    return EctopyPatterns(
        total=int(is_target.sum()),
        single=int((lengths == 1).sum()),
        pairs=int((lengths == 2).sum()),
        pair_beats=int(2 * (lengths == 2).sum()),
        runs=np.stack([runs_start[long_runs], runs_end[long_runs]], axis=1).astype(np.int64),
        run_beats=int(lengths[long_runs].sum()),
        bigeminy_episodes=int(bigeminy_spans.shape[0]),
        bigeminy_beats=int(bigeminy_mask.sum()),
        trigeminy_episodes=int(trigeminy_spans.shape[0]),
        trigeminy_beats=int(trigeminy_mask.sum()),
        pair_mask=pair_mask,
        run_mask=run_mask,
        bigeminy_mask=bigeminy_mask,
        trigeminy_mask=trigeminy_mask,
        bigeminy_spans=bigeminy_spans,
        trigeminy_spans=trigeminy_spans,
    )


def _runs(is_target: Mask, link: Mask) -> tuple[Indices, Indices]:
    """Primer y último índice de cada tramo de latidos objetivo contiguos."""
    n = int(is_target.size)
    if n == 0:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    joined = is_target[:-1] & is_target[1:] & link
    starts = is_target & ~np.concatenate(([False], joined))
    ends = is_target & ~np.concatenate((joined, [False]))
    return np.flatnonzero(starts).astype(np.int64), np.flatnonzero(ends).astype(np.int64)


def _cycles(
    labels: NDArray[np.uint8], is_target: Mask, link: Mask, *, period: int
) -> tuple[Mask, NDArray[np.int64]]:
    """Latidos ectópicos de los ciclos `N...N, X` repetidos `CYCLES_MIN` veces o más.

    Un ciclo empieza en `i`: `period - 1` normales seguidas de un objetivo, todo
    en el mismo tramo continuo. Los ciclos se encadenan de a `period` latidos,
    con el vínculo entre el objetivo de uno y la primera normal del siguiente.
    Devuelve la máscara de los objetivos de los patrones y, por patrón, el
    primer y el último índice (de la `N` del primer ciclo al objetivo del último).
    """
    n = int(labels.size)
    mask = np.zeros(n, dtype=bool)
    spans = np.empty((0, 2), dtype=np.int64)
    if n < period * CYCLES_MIN:
        return mask, spans
    count = n - period + 1
    is_normal = labels == CODE_N
    cycle = is_target[period - 1 :].copy()
    for step in range(period - 1):
        cycle &= is_normal[step : step + count] & link[step : step + count]

    found: list[tuple[int, int]] = []
    for residue in range(period):
        series = cycle[residue::period]
        if series.size < CYCLES_MIN:
            continue
        starts = residue + period * np.arange(series.size)
        # El vínculo entre dos ciclos consecutivos es el del objetivo del
        # primero con la normal del segundo.
        between = link[np.clip(starts[:-1] + period - 1, 0, link.size - 1)] if link.size else None
        if between is None:
            continue
        chained = series[:-1] & series[1:] & between
        begins = series & ~np.concatenate(([False], chained))
        ends = series & ~np.concatenate((chained, [False]))
        for first, last in zip(np.flatnonzero(begins), np.flatnonzero(ends), strict=True):
            if last - first + 1 >= CYCLES_MIN:
                found.append((int(starts[first]), int(starts[last]) + period - 1))
    found.sort()
    for begin, finish in found:
        mask[begin + period - 1 : finish + 1 : period] = True
    if found:
        spans = np.asarray(found, dtype=np.int64)
    return mask, spans


def alert_for_run(kind: str, severity: ECGEventSeverity) -> str | None:
    """Texto del aviso de una corrida, o None si no debe avisar a su severidad.

    La TV avisa siempre; la TPS, solo sostenida (HIGH). Lo usa el empalme
    (`ml_persistence`), que sube la severidad al cruzar los 30 s y necesita el
    texto aunque el bloque nuevo no lo traiga.
    """
    if kind == KIND_VT_RUN:
        return ALERT_VT
    if kind == KIND_SVT_RUN and _SEVERITY_RANK[severity] >= _SEVERITY_RANK[ECGEventSeverity.HIGH]:
        return ALERT_SVT
    return None


def run_severity(kind: str, sustained: bool) -> ECGEventSeverity:
    """Severidad de una corrida según su tipo y si es sostenida."""
    if kind == KIND_VT_RUN:
        return ECGEventSeverity.CRITICAL if sustained else ECGEventSeverity.HIGH
    return ECGEventSeverity.HIGH if sustained else ECGEventSeverity.MEDIUM


def ectopy_findings(
    *,
    rpeaks: Indices,
    labels: NDArray[np.uint8],
    contiguous: Mask,
    owned: Mask,
    ventricular_scores: Floats,
    prematurity: Floats,
    firmware_peaks: Indices,
    firmware_tolerance: int,
    sample_rate: int,
    budget: EpisodeBudget,
    classifier: str,
) -> list[Finding]:
    """Corridas y episodios de ectópicas del bloque. Coordenadas **relativas al lote**.

    `owned` marca los latidos de la parte nueva: un grupo sin ninguno ya lo
    informó el bloque anterior (misma regla que `episodes.group_beats`).
    `ventricular_scores` es el score de morfología por latido (NaN si no lo
    tiene) y `prematurity` el R-R sobre el esperado: puntúan los episodios V y
    S respectivamente (comparten el tope de anomalías, `episodes.enforce_budget`).
    """
    findings: list[Finding] = []
    for target in ("S", "V"):
        patterns = ectopy_patterns(labels, contiguous, target)
        if patterns.total == 0:
            continue
        in_tachy = np.zeros(labels.size, dtype=bool)
        for first, last in patterns.runs:
            run = _run_finding(
                rpeaks,
                int(first),
                int(last),
                target=target,
                owned=owned,
                firmware_peaks=firmware_peaks,
                firmware_tolerance=firmware_tolerance,
                sample_rate=sample_rate,
                classifier=classifier,
            )
            if run is not None:
                findings.append(run)
                in_tachy[first : last + 1] = True
        findings.extend(
            _episode_findings(
                rpeaks,
                labels,
                target=target,
                patterns=patterns,
                excluded=in_tachy,
                owned=owned,
                scores=ventricular_scores if target == "V" else None,
                prematurity=prematurity,
                sample_rate=sample_rate,
                budget=budget,
                classifier=classifier,
            )
        )
    return findings


def _run_finding(
    rpeaks: Indices,
    first: int,
    last: int,
    *,
    target: str,
    owned: Mask,
    firmware_peaks: Indices,
    firmware_tolerance: int,
    sample_rate: int,
    classifier: str,
) -> Finding | None:
    if not owned[first : last + 1].any():
        return None
    beats = rpeaks[first : last + 1]
    span = int(beats[-1] - beats[0])
    if span <= 0:
        return None
    bpm = 60.0 * (beats.size - 1) / (span / sample_rate)
    if bpm <= RUN_MIN_BPM:
        return None
    seconds = span / sample_rate
    sustained = seconds >= SUSTAINED_SECONDS
    kind = KIND_VT_RUN if target == "V" else KIND_SVT_RUN
    severity = run_severity(kind, sustained)
    margin = int(RUN_MARGIN_MS * sample_rate / 1000)
    start = max(int(beats[0]) - margin, 0)
    confirmed = _firmware_fraction(beats, firmware_peaks, firmware_tolerance)
    # La TV es lo que más falsos produce con electrodos secos: el push de una
    # no sostenida exige que el detector del firmware vea los mismos R.
    push = sustained or target == "S" or confirmed >= VT_PUSH_FIRMWARE_FRACTION
    rates = 60.0 * sample_rate / np.diff(beats)
    return Finding(
        kind=kind,
        event_type=ECGEventType.TACHYCARDIA,
        severity=severity,
        start_sample=start,
        length_samples=max(span + 2 * margin, 1),
        dedupe_key=f"{kind}:{start}",
        beat_count=int(beats.size),
        alert_message=alert_for_run(kind, severity),
        push=push,
        beat_samples=tuple(int(sample) for sample in beats),
        metadata={
            "peakBpm": round(float(rates.max()), 2),
            "medianBpm": round(float(np.median(rates)), 2),
            "durationSeconds": round(seconds, 2),
            "sustained": int(sustained),
            "firmwareConfirmedPct": round(100.0 * confirmed, 1),
            "classifier": classifier,
        },
    )


def _firmware_fraction(beats: Indices, firmware_peaks: Indices, tolerance: int) -> float:
    """Fracción de los R de `beats` que tiene un pico del firmware a menos de `tolerance`."""
    if firmware_peaks.size == 0 or beats.size == 0:
        return 0.0
    position = np.searchsorted(firmware_peaks, beats)
    below = firmware_peaks[np.clip(position - 1, 0, firmware_peaks.size - 1)]
    above = firmware_peaks[np.clip(position, 0, firmware_peaks.size - 1)]
    nearest = np.minimum(np.abs(beats - below), np.abs(above - beats))
    return float((nearest <= tolerance).mean())


def _episode_findings(
    rpeaks: Indices,
    labels: NDArray[np.uint8],
    *,
    target: str,
    patterns: EctopyPatterns,
    excluded: Mask,
    owned: Mask,
    scores: Floats | None,
    prematurity: Floats,
    sample_rate: int,
    budget: EpisodeBudget,
    classifier: str,
) -> list[Finding]:
    code = CODE_S if target == "S" else CODE_V
    candidates = np.flatnonzero((labels == code) & ~excluded)
    if candidates.size < EPISODE_MIN_BEATS:
        return []
    pre = int(round(BEAT_PRE_MS * sample_rate / 1000))
    post = int(round(BEAT_POST_MS * sample_rate / 1000))
    max_gap = int(5.0 * sample_rate)

    groups: list[list[int]] = [[int(candidates[0])]]
    for index in candidates[1:]:
        current, previous = int(index), groups[-1][-1]
        # Mismo criterio que `episodes.group_beats`: el hueco se mide en latidos
        # (un bigeminismo alterna normal/ectópico) con un tope temporal contra
        # los latidos que el gate sacó.
        if current - previous <= budget.gap_beats and (
            int(rpeaks[current]) - int(rpeaks[previous]) <= max_gap
        ):
            groups[-1].append(current)
        else:
            groups.append([current])

    kind = KIND_VENTRICULAR if target == "V" else KIND_SUPRAVENTRICULAR
    findings: list[Finding] = []
    for group in groups:
        if len(group) < EPISODE_MIN_BEATS or not owned[group].any():
            continue
        members = np.asarray(group)
        in_pair = bool(patterns.pair_mask[members].any() or patterns.run_mask[members].any())
        in_pattern = int(patterns.bigeminy_mask[members].sum()) + int(
            patterns.trigeminy_mask[members].sum()
        )
        severity = (
            ECGEventSeverity.MEDIUM if in_pair or in_pattern >= CYCLES_MIN else ECGEventSeverity.LOW
        )
        start = max(int(rpeaks[group[0]]) - pre, 0)
        end = int(rpeaks[group[-1]]) + post
        if scores is not None:
            finite = scores[members][np.isfinite(scores[members])]
            score = float(finite.max()) if finite.size else 0.5
        else:
            score = float(np.clip(1.0 - np.nanmin(prematurity[members]), 0.0, 1.0))
        findings.append(
            Finding(
                kind=kind,
                event_type=ECGEventType.ECTOPY,
                severity=severity,
                start_sample=start,
                length_samples=max(end - start, 1),
                dedupe_key=f"{kind}:{start}",
                score=round(score, 6),
                beat_count=len(group),
                metadata={
                    "pairBeats": int(patterns.pair_mask[members].sum()),
                    "bigeminyBeats": int(patterns.bigeminy_mask[members].sum()),
                    "trigeminyBeats": int(patterns.trigeminy_mask[members].sum()),
                    "classifier": classifier,
                },
                beat_samples=tuple(int(rpeaks[index]) for index in group),
            )
        )
    return findings
