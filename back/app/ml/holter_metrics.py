"""Métricas del informe Holter a partir de los latidos detectados.

Es una función pura sobre lo que ya está persistido: los latidos
(`app/ml/beats.py`), los tramos de la línea de tiempo y los eventos de calidad.
No relee señal, así que recalcular cuesta lo mismo que leer los latidos.

Reglas que atraviesan todo el módulo:

- **Un RR solo existe entre dos latidos del mismo tramo continuo.** Un corte de
  la línea de tiempo, un hueco interno o una exclusión entre dos latidos lo
  invalida. Por eso un hueco de registro nunca se cuenta como pausa.
- **Exclusiones:** los tramos `lead_off`, `sqi_unanalyzable` y `adc_saturated`
  (reglas de `INTEGRACION.md` §4.5), desde la versión 2 del algoritmo las
  ventanas que el motor de detección marcó como ruido
  (`QUALITY_EXCLUSION_REASONS`), y desde la 3 las que declaró riel
  (`HARDWARE_QUALITY_REASONS`), que llegan con el hardware. Sus latidos no se
  cuentan.
- **Las pausas no miran el ruido del motor.** Una asistolia es señal sin QRS, y
  el gate de calidad la marca `bad` por pSQI o basSQI igual que al ruido: sus
  índices son cocientes de potencia y no ven la amplitud. Medido de punta a
  punta: con el ruido excluido, una asistolia de 12-26 s desaparecía del
  informe. Las pausas se buscan entonces con las exclusiones del hardware
  solamente —como en la versión 1— y el médico las verifica en su tira. Un
  riel no es ruido sino señal que falta: entra con el hardware y sí corta.
- **Clasificación de latidos (S y V).** Salen de **otro flujo** que la FC, la VFC
  y el ST: las etiquetas por latido del motor (`beat_labels.py`), que lo
  produce con el gate de calidad y el banco de plantillas que la pasada de
  latidos no tiene. Son dos detectores distintos: `classifiedBeats` (N + S + V)
  no coincide con `totalBeats`, y el «por mil» de S y V es sobre lo clasificado.
  Los patrones (aislada, par, corrida, bigeminismo, trigeminismo) los define
  `ectopy.ectopy_patterns`, la misma función que usa el motor para sus
  hallazgos. Un estudio sin etiquetas (formato viejo, analizado en parte antes
  del clasificador) informa S y V como no disponibles con una razón; nunca ceros.
  La VFC usa como NN los RR que pasan el filtro de artefactos y ectópicos
  habitual: rango fisiológico y no más de 20 % de diferencia con la mediana
  local.
- **Correcciones del médico** (`DoctorCorrections`, #15). El ruido que marcó
  entra con las exclusiones del hardware (sale de todo, pausas incluidas), un
  hallazgo de ruido del motor que rechazó se resta del ruido, una pausa que
  rechazó no cuenta y las reclasificaciones re-etiquetan los latidos de su
  tramo antes de contar. Sobre un latido, la última corrección gana; el ruido
  del médico gana sobre cualquier reetiqueta.

Los valores se redondean a una cantidad fija de decimales: el informe final
congela este resultado en un snapshot cuyo hash tiene que ser reproducible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import median_filter

from app.ml import hrv
from app.ml.beat_labels import CODE_N, CODE_S, CODE_V, BeatLabelStream
from app.ml.ectopy import EctopyPatterns, ectopy_patterns
from app.ml.holter_contracts import BEAT_CLASSIFICATION_PARTIAL as BEAT_CLASSIFICATION_PARTIAL
from app.ml.holter_contracts import BEAT_CLASSIFICATION_PENDING as BEAT_CLASSIFICATION_PENDING

# Viven en un módulo sin numpy porque `studies_service` los importa al arrancar
# la API; se reexportan para que este siga siendo el lugar donde buscarlos.
from app.ml.holter_contracts import BREAK_KINDS as BREAK_KINDS
from app.ml.holter_contracts import ECTOPY_UNAVAILABLE as ECTOPY_UNAVAILABLE
from app.ml.holter_contracts import EXCLUSION_KINDS as EXCLUSION_KINDS
from app.ml.holter_contracts import HARDWARE_QUALITY_REASONS as HARDWARE_QUALITY_REASONS
from app.ml.holter_contracts import QUALITY_EXCLUSION_REASONS as QUALITY_EXCLUSION_REASONS
from app.ml.holter_contracts import DoctorCorrections as DoctorCorrections
from app.ml.holter_contracts import TimelineRun as TimelineRun

#: Viaja en `analysis.algorithmVersion` y queda congelado en el snapshot del
#: informe final. Cambia cuando cambia qué señal entra a las métricas, no solo
#: cómo se calculan: el PDF elige con él la nota de método, así que un informe
#: ya emitido sigue describiendo el algoritmo que lo produjo.
#:
#: - 1: Pan-Tompkins con las exclusiones de la Capa A.
#: - 2: además, sin las ventanas que el motor marcó como ruido
#:   (`QUALITY_EXCLUSION_REASONS`) salvo para las pausas, y sin RR que crucen
#:   un `frame_gap`.
#: - 3: además, sin los rieles que el motor declaró señal que falta
#:   (`HARDWARE_QUALITY_REASONS`), **pausas incluidas**: llegan desde
#:   `studies_service` como un tramo del hardware. Con la 2, un riel sin
#:   `LEAD_OFF` (segmento viejo, ADC congelado, corto) salía como una pausa de
#:   lo que durara, que el motor nunca afirmó ni avisó.
#:
#: - 4: los latidos salen del detector del motor (NeuroKit) y no del
#:   Pan-Tompkins por lote, que se enganchaba a la onda T (FC máxima doble) y
#:   quedaba ciego tras un artefacto (`INTEGRACION.md`, pendiente 2). Sin el
#:   veredicto del motor: las exclusiones de la Capa A, como la 1.
#: - 5: la 4 con el veredicto del motor, como la 3, y una pausa que cae en
#:   ruido cuenta solo si el motor la confirmó con su regla del hueco quieto;
#:   si no, se informa aparte (`noisyCount`, `noisyItems`; pendiente 3).
#:
#: - 6: la 5 con las S y V del clasificador de latidos (`beat_labels.py`) y las
#:   correcciones del médico (ruido, pausas rechazadas, reclasificaciones). Sin
#:   etiquetas utilizables (formato viejo, parciales o pendientes) se informa la
#:   5: la nota de método no puede afirmar una clasificación que no se usó.
#:
#: Es la versión vigente. Un cálculo concreto informa la 4 si no recibió el
#: veredicto del motor (`noise=None`: apagado, o una señal que no evaluó
#: entera), porque entonces la nota de método no puede afirmar una exclusión
#: que no se aplicó: `studies_service` tampoco le pasa los rieles ni las pausas
#: del motor.
#:
#: Un estudio cuyos latidos guardados salieron del Pan-Tompkins
#: (`legacy_beats`: analizado antes de la 4 y sin `backfill_beat_analysis
#: --reset`) se calcula y se declara como antes: la 3, o la 1 sin el motor. La
#: nota de método no puede nombrar un detector que no produjo esos latidos.
ALGORITHM_VERSION = 6
#: La 6 sin clasificación de latidos utilizable.
ALGORITHM_VERSION_WITHOUT_CLASSIFICATION = 5
#: Versión que se informa cuando el motor no cubrió la señal.
ALGORITHM_VERSION_WITHOUT_ENGINE = 4
#: Las mismas dos, para los latidos del Pan-Tompkins.
LEGACY_ALGORITHM_VERSION = 3
LEGACY_ALGORITHM_VERSION_WITHOUT_ENGINE = 1

PAUSE_MS = 2000
MIN_RR_S = 0.2
MAX_PAUSE_EVIDENCE = 10
#: FC mínima y máxima: promedio móvil de esta cantidad de NN consecutivos.
HR_WINDOW_BEATS = 8
NN_MIN_MS = 300
NN_MAX_MS = 2000
NN_MAX_CHANGE = 0.20
NN_MEDIAN_WINDOW = 11

ST_THRESHOLD_MV = 0.1
#: Un minuto entra a la serie de ST si tiene al menos esta cantidad de latidos.
ST_MIN_BEATS_PER_MINUTE = 5
#: Cinco latidos juntos no prueban que el ST se mantuvo durante un minuto.
ST_MIN_COVERAGE_MS = 55_000
#: Un tramo largo sin latidos medibles corta la evidencia de ese minuto.
ST_MAX_BEAT_GAP_MS = 5_000

RR_HISTOGRAM_START_MS = 300
RR_HISTOGRAM_END_MS = 2000
RR_HISTOGRAM_BIN_MS = 50

_MS_PER_MINUTE = 60_000
_MS_PER_HOUR = 3_600_000


def _round(value: float | None, digits: int) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _epoch_mapper(runs: Sequence[TimelineRun]) -> Callable[[np.ndarray], np.ndarray]:
    """Vectoriza `studies_service._wall_clock_resolver` sobre índices de muestra."""
    starts = np.array([run.start_sample for run in runs], dtype=np.int64)
    counts = np.array([run.sample_count for run in runs], dtype=np.int64)
    start_epochs = np.array([run.start_epoch_ms for run in runs], dtype=np.float64)
    end_epochs = np.array([run.end_epoch_ms for run in runs], dtype=np.float64)
    ends = starts + counts

    def resolve(samples: np.ndarray) -> np.ndarray:
        index = np.minimum(np.searchsorted(ends, samples, side="right"), len(runs) - 1)
        within = np.maximum(samples - starts[index], 0)
        span = (end_epochs[index] - start_epochs[index]) / np.maximum(counts[index], 1)
        return np.round(start_epochs[index] + within * span).astype(np.int64)

    return resolve


def _algorithm_version(with_engine: bool, legacy_beats: bool, classified: bool = False) -> int:
    if legacy_beats:
        return LEGACY_ALGORITHM_VERSION if with_engine else LEGACY_ALGORITHM_VERSION_WITHOUT_ENGINE
    if not with_engine:
        return ALGORITHM_VERSION_WITHOUT_ENGINE
    return ALGORITHM_VERSION if classified else ALGORITHM_VERSION_WITHOUT_CLASSIFICATION


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((s, e) for s, e in spans if e > s):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_spans(
    spans: Sequence[tuple[int, int]], cuts: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """`spans` sin los tramos de `cuts` (ambos se funden antes)."""
    remaining = _merge_spans(spans)
    for cut_start, cut_end in _merge_spans(cuts):
        pieces: list[tuple[int, int]] = []
        for start, end in remaining:
            if cut_end <= start or cut_start >= end:
                pieces.append((start, end))
                continue
            if start < cut_start:
                pieces.append((start, cut_start))
            if cut_end < end:
                pieces.append((cut_end, end))
        remaining = pieces
    return remaining


def _overlap(spans: list[tuple[int, int]], start: int, end: int) -> int:
    return sum(max(0, min(e, end) - max(s, start)) for s, e in spans)


def _evidence(
    value: float, sample: int, epoch: int, digits: int = 0, duration_ms: int | None = None
) -> dict[str, Any]:
    return {
        "value": _round(value, digits),
        "sampleIndex": int(sample),
        "epochMs": int(epoch),
        "durationMs": duration_ms,
    }


def _st_kind(
    minutes: np.ndarray,
    medians: np.ndarray,
    first_samples: np.ndarray,
    first_epochs: np.ndarray,
    last_epochs: np.ndarray,
    condition: np.ndarray,
) -> dict[str, Any]:
    """Episodios de un sentido (elevación o depresión) sobre la serie por minuto."""
    positions = np.flatnonzero(condition)
    if positions.size == 0:
        return {"episodes": 0, "durationSeconds": 0, "maxDeviation": None, "maxSlopeMvPerMin": None}
    breaks = (
        np.flatnonzero(
            (np.diff(minutes[positions]) != 1)
            | (first_epochs[positions[1:]] - last_epochs[positions[:-1]] > ST_MAX_BEAT_GAP_MS)
        )
        + 1
    )
    episodes = np.split(positions, breaks)
    slopes: list[float] = []
    for episode in episodes:
        first = int(episode[0])
        span = episode
        if (
            first > 0
            and minutes[first] - minutes[first - 1] == 1
            and first_epochs[first] - last_epochs[first - 1] <= ST_MAX_BEAT_GAP_MS
        ):
            span = np.concatenate(([first - 1], episode))
        if span.size >= 2:
            slopes.append(float(np.max(np.abs(np.diff(medians[span])))))
    magnitudes = np.abs(medians[positions])
    peak = int(positions[int(np.argmax(magnitudes))])
    return {
        "episodes": len(episodes),
        "durationSeconds": int(positions.size * 60),
        "maxDeviation": _evidence(
            abs(float(medians[peak])), first_samples[peak], first_epochs[peak], digits=2
        ),
        "maxSlopeMvPerMin": _round(max(slopes), 3) if slopes else None,
    }


def _st_channel(
    samples: np.ndarray, epochs: np.ndarray, st_mv: np.ndarray, channel: int, label: str
) -> dict[str, Any]:
    finite = np.isfinite(st_mv)
    samples, epochs, st_mv = samples[finite], epochs[finite], st_mv[finite]
    base = {"channel": channel, "label": label, "analyzedMinutes": 0, "medianLevelMv": None}
    empty_kind = {
        "episodes": 0,
        "durationSeconds": 0,
        "maxDeviation": None,
        "maxSlopeMvPerMin": None,
    }
    if st_mv.size == 0:
        return {**base, "elevation": empty_kind, "depression": empty_kind}
    minute_keys = epochs // _MS_PER_MINUTE
    keys, first, counts = np.unique(minute_keys, return_index=True, return_counts=True)
    keep = np.array(
        [
            count >= ST_MIN_BEATS_PER_MINUTE
            and epochs[start + count - 1] - epochs[start] >= ST_MIN_COVERAGE_MS
            and np.all(np.diff(epochs[start : start + count]) <= ST_MAX_BEAT_GAP_MS)
            for start, count in zip(first, counts, strict=True)
        ],
        dtype=bool,
    )
    medians = np.array(
        [float(np.median(st_mv[f : f + c])) for f, c in zip(first[keep], counts[keep], strict=True)]
    )
    minutes, first, counts = keys[keep], first[keep], counts[keep]
    if medians.size == 0:
        return {**base, "elevation": empty_kind, "depression": empty_kind}
    first_samples, first_epochs = samples[first], epochs[first]
    last_epochs = epochs[first + counts - 1]
    return {
        **base,
        "analyzedMinutes": int(medians.size),
        "medianLevelMv": _round(float(np.median(medians)), 3),
        "elevation": _st_kind(
            minutes, medians, first_samples, first_epochs, last_epochs, medians >= ST_THRESHOLD_MV
        ),
        "depression": _st_kind(
            minutes, medians, first_samples, first_epochs, last_epochs, medians <= -ST_THRESHOLD_MV
        ),
    }


def _hourly(
    beat_epochs: np.ndarray,
    nn: np.ndarray,
    nn_epochs: np.ndarray,
    window_hr: np.ndarray,
    window_epochs: np.ndarray,
) -> list[dict[str, Any]]:
    if beat_epochs.size == 0:
        return []
    first_hour = int(beat_epochs.min() // _MS_PER_HOUR)
    length = int(beat_epochs.max() // _MS_PER_HOUR) - first_hour + 1

    def bucket(epochs: np.ndarray) -> np.ndarray:
        clipped = np.clip(epochs // _MS_PER_HOUR - first_hour, 0, length - 1)
        return np.asarray(clipped, dtype=np.int64)

    beats = np.bincount(bucket(beat_epochs), minlength=length)
    nn_sum = np.bincount(bucket(nn_epochs), weights=nn, minlength=length)
    nn_count = np.bincount(bucket(nn_epochs), minlength=length)
    hr_min = np.full(length, np.inf)
    hr_max = np.full(length, -np.inf)
    if window_hr.size:
        np.minimum.at(hr_min, bucket(window_epochs), window_hr)
        np.maximum.at(hr_max, bucket(window_epochs), window_hr)
    hours: list[dict[str, Any]] = []
    for offset in range(length):
        if beats[offset] == 0:
            continue
        hours.append(
            {
                "hourStartEpochMs": (first_hour + offset) * _MS_PER_HOUR,
                "beats": int(beats[offset]),
                "avgBpm": (
                    _round(60_000 * nn_count[offset] / nn_sum[offset], 0)
                    if nn_count[offset]
                    else None
                ),
                "minBpm": _round(hr_min[offset], 0),
                "maxBpm": _round(hr_max[offset], 0),
            }
        )
    return hours


def _round_frequency(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    rounded: dict[str, Any] = {
        key: _round(result[key], 1)
        for key in ("totalPowerMs2", "ulfMs2", "vlfMs2", "lfMs2", "hfMs2")
    }
    rounded["lfHfRatio"] = _round(result["lfHfRatio"], 2)
    rounded["windows"] = result["windows"]
    rounded["spectrum"] = {
        "frequenciesHz": [round(value, 5) for value in result["spectrum"]["frequenciesHz"]],
        "powerMs2PerHz": [round(value, 2) for value in result["spectrum"]["powerMs2PerHz"]],
    }
    return rounded


def _clip_spans(spans: Sequence[tuple[int, int]], analyzed_until: int) -> list[tuple[int, int]]:
    return _merge_spans(
        [(max(0, s), min(e, analyzed_until)) for s, e in spans if s < analyzed_until]
    )


def _outside(samples: np.ndarray, spans: list[tuple[int, int]]) -> np.ndarray:
    """Máscara de los latidos que no caen dentro de ningún tramo (ya fundidos)."""
    if not spans:
        return np.ones(samples.size, dtype=bool)
    starts = np.array([s for s, _ in spans], dtype=np.int64)
    ends = np.array([e for _, e in spans], dtype=np.int64)
    position = np.searchsorted(starts, samples, side="right") - 1
    inside = (position >= 0) & (samples < ends[np.maximum(position, 0)])
    return np.asarray(~inside)


def _valid_rr(
    samples: np.ndarray,
    runs: Sequence[TimelineRun],
    excluded: list[tuple[int, int]],
    breaks: Sequence[int],
    rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    """`(rr_ms, válido)`: un RR vale si sus dos latidos caen en el mismo tramo continuo."""
    cuts = np.unique(
        np.array(
            [run.start_sample for run in runs]
            + [edge for span in excluded for edge in span]
            + list(breaks),
            dtype=np.int64,
        )
    )
    segment = np.searchsorted(cuts, samples, side="right")
    return np.diff(samples) * 1000 / rate, segment[1:] == segment[:-1]


@dataclass(frozen=True)
class _Classification:
    """Lo que salió de las etiquetas por latido, ya con las correcciones."""

    reason: str | None
    supraventricular: dict[str, Any] | None = None
    ventricular: dict[str, Any] | None = None
    classified_until: int = 0
    classified: int = 0
    unclassified: int = 0
    relabeled: int = 0
    abnormal: int = 0


def _ectopy_block(
    patterns: EctopyPatterns,
    code: int,
    labels: np.ndarray,
    samples: np.ndarray,
    epochs: np.ndarray,
    classified: int,
    rate: int,
) -> dict[str, Any]:
    """El recuadro S o V en el formato de `HolterEctopyOut`, con su evidencia."""
    longest = None
    if patterns.runs.shape[0]:
        sizes = patterns.runs[:, 1] - patterns.runs[:, 0] + 1
        first, last = patterns.runs[int(np.argmax(sizes))]
        longest = _evidence(
            float(sizes.max()),
            samples[first],
            epochs[first],
            duration_ms=int(round((samples[last] - samples[first]) * 1000 / rate)),
        )
    peak = None
    index = np.flatnonzero(labels == code)
    if index.size:
        # Ventana deslizante de 60 s que arranca en cada latido; en un empate, la
        # primera. Con minutos de reloj, 10 latidos que cruzan el cambio de
        # minuto contaban 5 + 5 y el máximo salía por debajo del real.
        index = index[np.argsort(epochs[index], kind="stable")]
        times = epochs[index]
        counts = np.searchsorted(times, times + _MS_PER_MINUTE, side="left") - np.arange(index.size)
        best = int(np.argmax(counts))
        first = int(index[best])
        last = int(index[best + counts[best] - 1])
        peak = _evidence(
            float(counts[best]),
            samples[first],
            epochs[first],
            duration_ms=int(epochs[last] - epochs[first]),
        )
    return {
        "total": patterns.total,
        "single": patterns.single,
        "pairs": {"episodes": patterns.pairs, "beats": patterns.pair_beats},
        "bigeminy": {
            "episodes": patterns.bigeminy_episodes,
            "beats": patterns.bigeminy_beats,
        },
        "trigeminy": {
            "episodes": patterns.trigeminy_episodes,
            "beats": patterns.trigeminy_beats,
        },
        "runs": {"episodes": int(patterns.runs.shape[0]), "beats": patterns.run_beats},
        "longestRun": longest,
        "perThousand": _round(1000 * patterns.total / classified, 1),
        "maxPerMinute": peak,
    }


def _classify(
    labels: BeatLabelStream | None,
    corrections: DoctorCorrections,
    *,
    usable: bool,
    excluded: list[tuple[int, int]],
    runs: Sequence[TimelineRun],
    breaks: Sequence[int],
    rate: int,
    analyzed_until: int,
    to_epoch: Callable[[np.ndarray], np.ndarray],
) -> _Classification:
    """Las cifras S y V, o la razón por la que no se pueden dar.

    - Sin etiquetas, o con latidos del formato viejo: `ECTOPY_UNAVAILABLE`.
    - Etiquetas que no cubren desde el inicio del estudio, o una señal que el
      motor no evaluó entera (`usable` falso): `BEAT_CLASSIFICATION_PARTIAL`.
      Contar S y V de un tramo y llamarlo el registro es peor que no darlas.
    - Etiquetas que todavía no llegaron a ninguna señal, o sin un solo latido
      clasificable: `BEAT_CLASSIFICATION_PENDING`.

    Con el estudio abierto el clasificador va unos bloques detrás de los
    latidos: se cuenta lo clasificado hasta `classifiedUntilSample` y esa
    cobertura viaja en `analysis`, en vez de dejar S y V en blanco hasta el cierre.
    Los latidos son los de las etiquetas, **sin los excluidos** (hardware, ruido
    del motor y ruido del médico), y los vínculos entre latidos contiguos son
    los mismos RR válidos que usa la FC: un hueco o una exclusión corta un patrón.
    """
    if labels is None:
        return _Classification(ECTOPY_UNAVAILABLE)
    if not usable or labels.from_sample > min(run.start_sample for run in runs):
        return _Classification(BEAT_CLASSIFICATION_PARTIAL)
    until = min(labels.until_sample, analyzed_until)
    if until <= labels.from_sample:
        return _Classification(BEAT_CLASSIFICATION_PENDING)

    records = labels.records
    samples = records["sample_index"].astype(np.int64)
    order = np.argsort(samples, kind="stable")
    records, samples = records[order], samples[order]
    keep = (samples < until) & _outside(samples, excluded)
    records, samples = records[keep], samples[keep]
    codes = records["label"].astype(np.uint8)
    original = codes.copy()
    for relabel in corrections.relabels:
        hit = (
            (samples >= relabel.start_sample)
            & (samples < relabel.end_sample)
            & (codes == ord(relabel.from_label))
        )
        codes[hit] = ord(relabel.to_label)

    classified = int(np.isin(codes, (CODE_N, CODE_S, CODE_V)).sum())
    if classified == 0:
        return _Classification(BEAT_CLASSIFICATION_PENDING, classified_until=until)
    _, contiguous = _valid_rr(samples, runs, excluded, breaks, rate)
    epochs = to_epoch(samples)
    s_patterns = ectopy_patterns(codes, contiguous, "S")
    v_patterns = ectopy_patterns(codes, contiguous, "V")
    return _Classification(
        None,
        supraventricular=_ectopy_block(
            s_patterns, CODE_S, codes, samples, epochs, classified, rate
        ),
        ventricular=_ectopy_block(v_patterns, CODE_V, codes, samples, epochs, classified, rate),
        classified_until=until,
        classified=classified,
        unclassified=int(codes.size - classified),
        relabeled=int((codes != original).sum()),
        abnormal=s_patterns.total + v_patterns.total,
    )


def compute_holter_metrics(
    beats: np.ndarray,
    runs: Sequence[TimelineRun],
    exclusions: Sequence[tuple[int, int]],
    breaks: Sequence[int],
    rate: int,
    analyzed_until: int,
    *,
    noise: Sequence[tuple[int, int]] | None = None,
    engine_pauses: Sequence[tuple[int, int]] = (),
    engine_episodes: Sequence[tuple[int, int]] = (),
    legacy_beats: bool = False,
    labels: BeatLabelStream | None = None,
    corrections: DoctorCorrections | None = None,
) -> dict[str, Any]:
    """Métricas del informe en el formato de `HolterMetricsOut` (camelCase).

    `exclusions` son los tramos del hardware (`EXCLUSION_KINDS`, más los
    rieles del motor desde la versión 3) y `noise` las ventanas que el motor
    marcó como ruido. `None` en `noise` quiere decir que el motor no evaluó la
    señal: el resultado es el del algoritmo 1 y lo declara. Con una lista
    —aunque esté vacía— es la vigente (`ALGORITHM_VERSION`).

    El ruido sale de todo salvo de las pausas (ver el docstring del módulo):
    sus latidos no se cuentan para la FC, la VFC ni los extremos, y un RR que
    lo cruza no es NN, pero una pausa se busca solo contra el hardware, los
    cortes y los huecos.

    Las pausas del motor (`engine_pauses`, `[R1, R2]` de cada una, decididas
    con la regla del hueco quieto de `quiet_gap`, que sí distingue una
    asistolia del ruido) cuentan siempre, con su duración, y reemplazan a las
    del detector que las solapan. Una del detector que toca el ruido y que el
    motor no afirmó es, lo más probable, un latido que no vio en el ruido: se
    informa aparte, en `noisyCount` y `noisyItems`, y no cuenta ni puede ser la
    más larga (`INTEGRACION.md`, pendiente 3).

    `engine_episodes` son eventos de pausa del motor que no son un solo R-R:
    varias pausas fundidas por la refractariedad (`episodes.apply_refractory`,
    el empalme entre bloques) o una abierta en un borde. Su largo no es la
    duración de ninguna pausa —dos de 4 y 3 s a menos de 10 s salen como un
    evento de 11 s—, así que no se cuentan como pausa: solo confirman las del
    detector que solapan, que cuentan con su propia duración.

    `legacy_beats` declara latidos del Pan-Tompkins: se calcula como la versión
    3 (o la 1), sin las pausas del motor y con las pausas en ruido contadas.

    `labels` son las etiquetas por latido del motor (de ahí salen S, V y los
    latidos anormales; ver el docstring del módulo) y `corrections` lo que el
    médico corrigió: se aplican antes de calcular nada, así que el resultado es
    el de los datos ya corregidos y no hay una segunda vía que pueda discrepar.
    """
    if not runs:
        raise ValueError("compute_holter_metrics necesita al menos un tramo")
    to_epoch = _epoch_mapper(runs)
    doctor = corrections or DoctorCorrections()
    # El ruido del médico es una exclusión del hardware: sale de todo, pausas
    # incluidas. Un ruido del motor que rechazó vuelve a ser señal legible.
    hardware = _clip_spans([*exclusions, *doctor.noise_spans], analyzed_until)
    if noise is not None:
        noise = _subtract_spans(noise, doctor.readable_spans)
    excluded = _clip_spans([*hardware, *(noise or ())], analyzed_until)
    rejected_pauses = _merge_spans(doctor.rejected_pause_spans)

    analyzable_samples = 0
    for run in runs:
        start = run.start_sample
        end = min(run.start_sample + run.sample_count, analyzed_until)
        if end > start:
            analyzable_samples += (end - start) - _overlap(excluded, start, end)
    analyzed_ms = int(analyzable_samples * 1000 / rate)

    samples = beats["sample_index"].astype(np.int64)
    st_levels = beats["st_mv"].astype(np.float64)
    order = np.argsort(samples, kind="stable")
    samples, st_levels = samples[order], st_levels[order]
    # Un latido justo en el borde entre dos pasadas de análisis puede salir de
    # las dos con una muestra de diferencia; ningún RR real dura menos de 200 ms.
    if samples.size:
        distinct = np.concatenate(([True], np.diff(samples) >= int(MIN_RR_S * rate)))
        samples, st_levels = samples[distinct], st_levels[distinct]
    in_range = samples < analyzed_until
    samples, st_levels = samples[in_range], st_levels[in_range]
    # Los latidos para las pausas: fuera del hardware, aunque caigan en ruido.
    pause_samples = samples[_outside(samples, hardware)]
    kept = _outside(samples, excluded)
    samples, st_levels = samples[kept], st_levels[kept]
    epochs = to_epoch(samples)

    classification = (
        _Classification(ECTOPY_UNAVAILABLE)
        if legacy_beats
        else _classify(
            labels,
            doctor,
            usable=noise is not None,
            excluded=excluded,
            runs=runs,
            breaks=breaks,
            rate=rate,
            analyzed_until=analyzed_until,
            to_epoch=to_epoch,
        )
    )
    analysis = {
        "algorithmVersion": _algorithm_version(
            noise is not None, legacy_beats, classification.reason is None
        ),
        "analyzedUntilSample": int(analyzed_until),
        "analyzedMs": analyzed_ms,
        "excludedMs": int(sum(e - s for s, e in excluded) * 1000 / rate),
        "rrIntervals": 0,
        "nnIntervals": 0,
        "classifiedUntilSample": classification.classified_until,
        "classifiedBeats": classification.classified,
        "unclassifiedBeats": classification.unclassified,
        "doctorCorrections": {
            "findings": doctor.corrected_findings,
            "noiseMs": int(
                sum(e - s for s, e in _clip_spans(doctor.noise_spans, analyzed_until)) * 1000 / rate
            ),
            "relabeledBeats": classification.relabeled,
        },
    }
    result: dict[str, Any] = {
        "status": "ok",
        "unavailableReason": None,
        "analysis": analysis,
        "heartRate": None,
        "pauses": {
            "thresholdMs": PAUSE_MS,
            "count": 0,
            "longest": None,
            "items": [],
            "noisyCount": 0,
            "noisyItems": [],
        },
        "supraventricular": classification.supraventricular,
        "ventricular": classification.ventricular,
        "ectopyUnavailableReason": classification.reason,
        "hrvTime": None,
        "hrvFrequency": None,
        "st": [_st_channel(samples, epochs, st_levels, 1, "Canal 1 (LL-RA)")],
        "hourly": [],
        "rrHistogram": {
            "startMs": RR_HISTOGRAM_START_MS,
            "binMs": RR_HISTOGRAM_BIN_MS,
            "counts": [0] * ((RR_HISTOGRAM_END_MS - RR_HISTOGRAM_START_MS) // RR_HISTOGRAM_BIN_MS),
        },
    }
    if samples.size < 2:
        result["status"] = "insufficient_data"
        result["unavailableReason"] = "NOT_ENOUGH_BEATS"
        return result

    # --- RR válidos ------------------------------------------------------- #
    rr_ms, rr_valid = _valid_rr(samples, runs, excluded, breaks, rate)
    analysis["rrIntervals"] = int(rr_valid.sum())

    # --- Pausas ------------------------------------------------------------ #
    # Contra el hardware solo: el ruido del motor no distingue una asistolia.
    pause_rr, pause_valid = _valid_rr(pause_samples, runs, hardware, breaks, rate)
    pause_epochs = to_epoch(pause_samples)
    pause_index = np.flatnonzero(pause_valid & (pause_rr > PAUSE_MS))
    # Las del motor primero: dentro de una asistolia el detector encuentra
    # "latidos" en el ruido y la parte en pausas más cortas, y el motor la
    # delimita entera con su regla del hueco quieto. Una pausa del detector que
    # solapa una del motor es la misma, y queda la del motor.
    # Los latidos del Pan-Tompkins se calculan como la versión 3: sin el motor
    # en las pausas, y las del ruido cuentan.
    with_engine = noise is not None and not legacy_beats
    noisy_spans = _clip_spans(noise or (), analyzed_until) if with_engine else []
    confirmed = (
        [
            (start, end)
            for start, end in _merge_spans(engine_pauses)
            if start < analyzed_until
            and (end - start) * 1000 / rate > PAUSE_MS
            and not _overlap(rejected_pauses, start, end)
        ]
        if with_engine
        else []
    )
    # Fundidas o abiertas: confirman, pero su largo no es el de ninguna pausa.
    episodes = _clip_spans(engine_episodes, analyzed_until) if with_engine else []
    pause_items: list[dict[str, Any]] = []
    if confirmed:
        starts = np.array([start for start, _ in confirmed], dtype=np.int64)
        for start, epoch, (_, end) in zip(starts, to_epoch(starts), confirmed, strict=True):
            duration = (end - int(start)) * 1000 / rate
            pause_items.append(_evidence(duration, start, epoch, duration_ms=round(duration)))
    noisy_items: list[dict[str, Any]] = []
    for k in pause_index:
        opens, closes = int(pause_samples[k]), int(pause_samples[k + 1])
        if _overlap(confirmed, opens, closes) or _overlap(rejected_pauses, opens, closes):
            continue
        item = _evidence(
            pause_rr[k], pause_samples[k], pause_epochs[k], duration_ms=int(round(pause_rr[k]))
        )
        noisy = _overlap(noisy_spans, opens, closes) and not _overlap(episodes, opens, closes)
        (noisy_items if noisy else pause_items).append(item)
    for items in (pause_items, noisy_items):
        items.sort(key=lambda item: (-item["durationMs"], item["sampleIndex"]))
    result["pauses"] = {
        "thresholdMs": PAUSE_MS,
        "count": len(pause_items),
        "longest": pause_items[0] if pause_items else None,
        "items": pause_items[:MAX_PAUSE_EVIDENCE],
        "noisyCount": len(noisy_items),
        "noisyItems": noisy_items[:MAX_PAUSE_EVIDENCE],
    }

    # --- NN ---------------------------------------------------------------- #
    candidate = np.flatnonzero(rr_valid & (rr_ms >= NN_MIN_MS) & (rr_ms <= NN_MAX_MS))
    nn_index = np.empty(0, dtype=np.int64)
    if candidate.size:
        local = median_filter(rr_ms[candidate], size=NN_MEDIAN_WINDOW, mode="nearest")
        nn_index = candidate[np.abs(rr_ms[candidate] - local) <= NN_MAX_CHANGE * local]
    nn = rr_ms[nn_index]
    nn_epochs = epochs[nn_index + 1]
    analysis["nnIntervals"] = int(nn.size)

    # --- FC ---------------------------------------------------------------- #
    window_hr = np.empty(0)
    window_epochs = np.empty(0, dtype=np.int64)
    window_samples = np.empty(0, dtype=np.int64)
    if nn.size >= HR_WINDOW_BEATS:
        width = HR_WINDOW_BEATS
        contiguous = nn_index[width - 1 :] - nn_index[: nn.size - width + 1] == width - 1
        sums = np.concatenate(([0.0], np.cumsum(nn)))
        means = (sums[width:] - sums[:-width]) / width
        centers = nn_index[width // 2 : width // 2 + means.size]
        window_hr = (60_000 / means)[contiguous]
        window_samples = samples[centers][contiguous]
        window_epochs = epochs[centers][contiguous]
    minutes = analyzed_ms / _MS_PER_MINUTE
    heart_rate: dict[str, Any] = {
        "averageBpm": _round(samples.size / minutes, 0) if minutes > 0 else None,
        "min": None,
        "max": None,
        "totalBeats": int(samples.size),
        "abnormalBeats": classification.abnormal if classification.reason is None else None,
        "abnormalPerThousand": (
            _round(1000 * classification.abnormal / classification.classified, 1)
            if classification.reason is None
            else None
        ),
        "windowBeats": HR_WINDOW_BEATS,
    }
    if window_hr.size:
        low, high = int(np.argmin(window_hr)), int(np.argmax(window_hr))
        heart_rate["min"] = _evidence(window_hr[low], window_samples[low], window_epochs[low])
        heart_rate["max"] = _evidence(window_hr[high], window_samples[high], window_epochs[high])
    result["heartRate"] = heart_rate

    # --- VFC --------------------------------------------------------------- #
    if nn.size >= 2:
        nn_times_s = (nn_epochs - nn_epochs[0]) / 1000
        adjacent = np.diff(nn_index) == 1
        successive = np.diff(nn)[adjacent]
        time = hrv.time_domain(nn, nn_times_s, successive)
        result["hrvTime"] = {
            "sdnnMs": _round(time["sdnnMs"], 1),
            "sdannMs": _round(time["sdannMs"], 1),
            "rmssdMs": _round(time["rmssdMs"], 1),
            "pnn50Percent": _round(time["pnn50Percent"], 1),
            "cv": _round(time["cv"], 3),
            "meanNnMs": _round(float(nn.mean()), 1),
        }
        result["hrvFrequency"] = _round_frequency(hrv.frequency_domain(nn, nn_times_s))

    result["hourly"] = _hourly(epochs, nn, nn_epochs, window_hr, window_epochs)
    edges = np.arange(RR_HISTOGRAM_START_MS, RR_HISTOGRAM_END_MS + 1, RR_HISTOGRAM_BIN_MS)
    counts, _ = np.histogram(nn, bins=edges)
    result["rrHistogram"]["counts"] = [int(value) for value in counts]
    return result
