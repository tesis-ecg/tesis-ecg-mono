"""Se y PPV latido a latido de los dos detectores de R del backend. Ver DETECTORS.md.

    cd back
    uv run python -m tools.physionet.evaluate --detectors --download   # baja lo que falte y sale
    uv run python -m tools.physionet.evaluate --detectors --jobs 8
    uv run python -m tools.physionet.evaluate --detectors --part mitdb --records 113 231
    uv run python -m tools.physionet.evaluate --detectors --summarize-only

`python -m tools.physionet.detectors ...` hace lo mismo.

En `app.ml.rpeak_detection` conviven dos detectores y todavía no se unificaron:

* `nk` — `detect_rpeaks(clean_signal(raw))`: NeuroKit sin corrección de artefactos. Es
  el del motor: alimenta el gate de calidad (bSQI), el ritmo y la morfología.
* `pt` — `detect_r_peaks(raw)`: el Pan-Tompkins escrito a mano. Alimenta las métricas
  Holter (`/holter-metrics`: FC, pausas, VFC, ST).

Cada uno recibe **lo que recibe en producción**: `nk` la señal de `clean_signal`, `pt`
los mV crudos. `nk` y `pt` corren sobre el registro entero de una vez; las variantes
(`VARIANTS`) los corren además como en producción —`nk` por bloques de 300 s, `pt` por
lotes de ~26 s, reaprendiendo el umbral en cada uno— y prueban un `pt` que reaprende
tras 5 s sin latidos.

Tres bancos de prueba, en las tres partes de `--part`:

* `mitdb` — MIT-BIH Arrhythmia, canal 0 llevado a 500 Hz con el mismo `load_record` de
  `evaluate.py`. Referencia: las anotaciones de latido (`BEAT_SYMBOLS`).
* `nstdb` — 118 y 119 con ruido de movimiento de electrodo a seis SNR, puntuados por
  separado en los bloques con ruido y en los limpios (`evaluate.noisy_mask`).
* `vest` — las capturas de canal 2 del chaleco contra el tren del firmware, solo dentro
  de las ventanas GOOD del gate (`tools.vest.detectors`).

Emparejamiento: 1 a 1, de **cardinalidad máxima** y, entre las máximas, de menor error
absoluto total (`match_beats`). MIT-BIH se puntúa con los parámetros por defecto de
`bxb`, que son los de ANSI/AAMI EC57: desde los 5 min (`-f`) y con una ventana de
±150 ms (`-w 0.15`, la máxima diferencia **absoluta** entre anotaciones). Además, con
criterios más estrictos que `bxb`: desde el segundo 1 y a ±150, ±75 y ±50 ms. Un par a
±150 ms que queda a más de 50 ms de la anotación se cuenta aparte, como error de
ubicación del fiducial y no de detección. **Nada se ajusta sobre estas etiquetas**.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ml.rpeak_detection import clean_signal, detect_r_peaks, detect_rpeaks  # noqa: E402
from tools.physionet.evaluate import (  # noqa: E402
    DATA_DIR,
    NOISE_SNR,
    SOURCE_RATE,
    TARGET_RATE,
    download,
    download_noise,
    load_record,
    noisy_mask,
)

RESULTS_PATH = DATA_DIR / "detectors_results.json"

#: Los 48 registros de MIT-BIH Arrhythmia.
MITDB_RECORDS = (
    *("100", "101", "102", "103", "104", "105", "106", "107", "108", "109"),
    *("111", "112", "113", "114", "115", "116", "117", "118", "119"),
    *("121", "122", "123", "124"),
    *("200", "201", "202", "203", "205", "207", "208", "209", "210"),
    *("212", "213", "214", "215", "217", "219", "220", "221", "222", "223"),
    *("228", "230", "231", "232", "233", "234"),
)
#: Con marcapasos. EC57 permite excluirlos, y la literatura casi siempre reporta los 44
#: restantes: se dan los dos totales.
PACED = frozenset(("102", "104", "107", "217"))
#: Los 30 que estaban bajados cuando se hizo el primer sondeo (pt Se 0,9761 / PPV
#: 0,9801, nk Se 0,9837 / PPV 0,9703): el total sobre este subconjunto es el comparable.
PROBE_RECORDS = frozenset(
    (
        *("100", "101", "102", "103", "104", "105", "106", "107", "108", "109"),
        *("111", "113", "114", "115", "116", "118", "119"),
        *("201", "202", "203", "205", "207", "208", "209"),
        *("217", "221", "228", "230", "231", "233"),
    )
)
NSTDB_BASES = ("118", "119")

#: Etiquetas de latido de MIT-BIH, las mismas que `wfdb` y `bxb` tratan como latido:
#: normal y bloqueos de rama (N L R B), supraventriculares (A a J S), ventriculares
#: (V r E), fusión (F), escapes (e j n), estimulados (/ f) e inclasificable (Q ?).
#: Lo demás no es un latido y no entra: ritmo (+), calidad (~), artefacto aislado
#: (|), onda P no conducida (x), ondas de flutter ventricular (!), comentarios (").
BEAT_SYMBOLS = frozenset("NLRBAaJSVrFejnE/fQ?")
#: Inicio y fin de flutter/fibrilación ventricular. EC57 excluye esos tramos de la
#: estadística de QRS: ahí no hay complejos que detectar (en MIT-BIH, solo 207).
VF_START, VF_END = "[", "]"

#: ±150 ms es la ventana por defecto de `bxb` (`-w 0.15`), la de EC57; ±75 y ±50 ms son
#: más estrictas y además miden dónde ubica el R cada detector.
TOLERANCES_MS = (150.0, 75.0, 50.0)
#: `bxb` arranca la comparación a los 5 min (`-f`): es el período de aprendizaje que
#: EC57 le concede al detector. Cambiarlo es una "non-standard comparison".
EC57_START_S = 300.0
#: El criterio estricto descarta solo el primer y el último segundo: el transitorio
#: de los filtros de fase cero. El arranque del umbral sí cuenta ahí.
EDGE_S = 1.0
#: Un par a ±150 ms con |desvío| mayor que esto es un error de ubicación del
#: fiducial: el latido se detectó, pero el R quedó sobre otra parte del complejo.
FIDUCIAL_MS = 50.0
#: Cómo se puntúa MIT-BIH: (nombre, región, tolerancia). El primero es EC57, lo que
#: hace `bxb` sin opciones; los demás, más estrictos.
CRITERIA = (
    ("EC57: desde 5 min, ±150 ms", "ec57", "150"),
    ("desde 1 s, ±150 ms", "todo", "150"),
    ("desde 1 s, ±75 ms", "todo", "75"),
    ("desde 1 s, ±50 ms", "todo", "50"),
)
#: Los criterios con tabla de los 5 peores y diagnóstico del modo de falla.
WORST_CRITERIA = (("ec57", "150"), ("todo", "75"))
#: Pan-Tompkins con reaprendizaje (`detect_pt_rearmed`): tras esto sin latidos,
#: vuelve a arrancar. Más largo que cualquier R-R sinusal y que la pausa de 3 s.
REARM_S = 5.0
#: Cómo corre `pt` en producción (`processing.append_beat_analysis`): una pasada por
#: lote, sobre la señal nueva más `BEAT_CONTEXT_SECONDS` (30 s) a cada lado. Un lote
#: es un POST del puente, hasta 48 tramas de 256 B: a los 468,6 B/s medidos son
#: ~26 s de señal (menos con el chaleco flojo, que comprime peor).
BATCH_S = 26.0
BATCH_CONTEXT_S = 30.0
#: Cómo corre `nk` en producción (`ml_analysis_*_seconds`): bloques de 300 s, cada uno
#: limpiado y detectado con 60 s de contexto antes y 30 s de lookahead después.
BLOCK_S, BLOCK_CONTEXT_S, BLOCK_LOOKAHEAD_S = 300.0, 60.0, 30.0

#: Ventanas de la clasificación de errores (`diagnose`), en ms.
NEAR_MS = 150.0
T_WAVE_MS = (150.0, 450.0)
#: Un FN con una detección a menos de esto no es un latido perdido sino uno corrido.
SHIFTED_MS = 300.0
#: Medio ancho de la ventana donde se mide la amplitud del QRS de referencia.
AMPLITUDE_HALF_MS = 50.0

Detector = Callable[[np.ndarray, int], np.ndarray]


def detect_nk(raw_mv: np.ndarray, rate: int) -> np.ndarray:
    """El del motor: NeuroKit sobre la señal filtrada, como en `pipeline.analyze_batch`."""
    return detect_rpeaks(clean_signal(raw_mv, rate), rate)


def detect_pt(raw_mv: np.ndarray, rate: int) -> np.ndarray:
    """El de `/holter-metrics`: Pan-Tompkins sobre los mV crudos, como en `beats.py`."""
    return detect_r_peaks(raw_mv, rate)


def detect_pt_rearmed(raw_mv: np.ndarray, rate: int, silence_s: float = REARM_S) -> np.ndarray:
    """Variante de `pt` que **no está en producción**: reaprende el umbral si se queda ciego.

    En `_classify_candidates` el nivel de señal (`spki`) solo se mueve cuando se acepta
    un latido. Un evento de energía grande (el riel del AFE, un escalón de línea de
    base) que se acepta como QRS lo deja arriba, el umbral ya no baja y `pt` no ve
    nada más hasta el final de la pasada. Acá, si pasan `silence_s` sin latidos, se lo
    vuelve a correr desde ahí: reaprende `spki` y `npki` con los 8 s siguientes. Es la
    medición que pide la unificación, no una propuesta de implementación.
    """
    silence = int(silence_s * rate)
    found: list[np.ndarray] = []
    start = 0
    while raw_mv.size - start >= 2 * rate:
        peaks = detect_r_peaks(raw_mv[start:], rate) + start
        marks = np.concatenate(([start], peaks, [raw_mv.size]))
        gaps = np.flatnonzero(np.diff(marks) > silence)
        if gaps.size == 0:
            found.append(peaks)
            break
        quiet_from = int(marks[gaps[0]])
        found.append(peaks[peaks <= quiet_from])
        start = quiet_from + silence
    return np.unique(np.concatenate(found)) if found else np.empty(0, dtype=np.int64)


def detect_pt_batched(
    raw_mv: np.ndarray,
    rate: int,
    batch_s: float = BATCH_S,
    context_s: float = BATCH_CONTEXT_S,
) -> np.ndarray:
    """`pt` como lo corre producción: por lote, y reaprendiendo el umbral en cada uno.

    `append_beat_analysis` analiza cada lote por separado: lee la señal nueva con 30 s
    de contexto a cada lado, corre `detect_r_peaks` sobre eso (que vuelve a estimar
    `spki` y `npki` con sus primeros 8 s, dentro del contexto) y se queda con los
    latidos de la parte nueva. Así, lo que el umbral arrastra dura como mucho una
    pasada, pero el arranque se repite en cada una.
    """
    step = int(batch_s * rate)
    context = int(context_s * rate)
    found: list[np.ndarray] = []
    for start in range(0, raw_mv.size, step):
        end = min(start + step, raw_mv.size)
        low, high = max(start - context, 0), min(end + context, raw_mv.size)
        peaks = detect_r_peaks(raw_mv[low:high], rate) + low
        found.append(peaks[(peaks >= start) & (peaks < end)])
    return np.concatenate(found) if found else np.empty(0, dtype=np.int64)


def detect_nk_blocked(raw_mv: np.ndarray, rate: int) -> np.ndarray:
    """`nk` como lo corre el motor: por bloques de 300 s con su contexto y su lookahead."""
    step = int(BLOCK_S * rate)
    before, after = int(BLOCK_CONTEXT_S * rate), int(BLOCK_LOOKAHEAD_S * rate)
    found: list[np.ndarray] = []
    for start in range(0, raw_mv.size, step):
        end = min(start + step, raw_mv.size)
        low, high = max(start - before, 0), min(end + after, raw_mv.size)
        peaks = detect_nk(raw_mv[low:high], rate) + low
        found.append(peaks[(peaks >= start) & (peaks < end)])
    return np.concatenate(found) if found else np.empty(0, dtype=np.int64)


DETECTORS: dict[str, Detector] = {"nk": detect_nk, "pt": detect_pt}
DETECTOR_LABELS = {
    "nk": "nk = `detect_rpeaks` (NeuroKit, motor)",
    "pt": "pt = `detect_r_peaks` (Pan-Tompkins, métricas Holter)",
}
#: Variantes que no están en producción: se puntúan aparte, en su propia sección.
VARIANTS: dict[str, Detector] = {
    "nk_por_bloque": detect_nk_blocked,
    "pt_por_lote": detect_pt_batched,
    "pt_reinicio": detect_pt_rearmed,
}
VARIANT_LABELS = {
    "nk_por_bloque": f"nk_por_bloque = `nk` como en producción: bloques de {BLOCK_S:g} s con"
    f" {BLOCK_CONTEXT_S:g} s de contexto y {BLOCK_LOOKAHEAD_S:g} s de lookahead",
    "pt_por_lote": f"pt_por_lote = `pt` como en producción: por lotes de {BATCH_S:g} s con"
    f" {BATCH_CONTEXT_S:g} s de contexto a cada lado, reaprendiendo el umbral en cada uno",
    "pt_reinicio": f"pt_reinicio = `pt` sobre el registro entero, que vuelve a arrancar tras"
    f" {REARM_S:g} s sin latidos (no está en producción)",
}


# --------------------------------------------------------------------------- #
# Emparejamiento y puntaje
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Match:
    #: Por latido de referencia / por detección: si quedó emparejado.
    reference_matched: np.ndarray
    detected_matched: np.ndarray
    #: Detección − referencia, en muestras, de cada par, en orden de referencia.
    pair_reference: np.ndarray
    offsets: np.ndarray


def match_beats(reference: np.ndarray, detected: np.ndarray, tolerance: float) -> Match:
    """Emparejamiento 1 a 1 óptimo: máximos pares con |d − r| ≤ `tolerance` muestras.

    Se parte la unión ordenada de los dos trenes donde dos puntos consecutivos quedan a
    más de `tolerance`: ningún par puede cruzar ese hueco, así que cada tramo se
    resuelve solo. Casi todos son un latido y una detección; los que no —un doble
    disparo, dos detecciones alrededor de un QRS— van por el algoritmo húngaro
    (`linear_sum_assignment`) con costo |d − r| y un costo prohibitivo fuera de la
    tolerancia, mayor que la suma de cualquier conjunto de pares válidos: así
    maximiza primero la cantidad de pares y después minimiza el error. Un greedy
    "al más cercano" puede perder un par; un greedy por orden temporal no pierde
    ninguno pero empareja mal y ensucia el desvío.
    """
    from scipy.optimize import linear_sum_assignment

    reference = np.asarray(reference, dtype=np.int64)
    detected = np.asarray(detected, dtype=np.int64)
    reference_matched = np.zeros(reference.size, dtype=bool)
    detected_matched = np.zeros(detected.size, dtype=bool)
    pairs: list[tuple[int, int]] = []
    if reference.size and detected.size:
        points = np.concatenate((reference, detected))
        is_reference = np.concatenate(
            (np.ones(reference.size, dtype=bool), np.zeros(detected.size, dtype=bool))
        )
        index = np.concatenate((np.arange(reference.size), np.arange(detected.size)))
        order = np.argsort(points, kind="stable")
        cuts = np.flatnonzero(np.diff(points[order]) > tolerance) + 1
        for group in np.split(order, cuts):
            refs = index[group][is_reference[group]]
            dets = index[group][~is_reference[group]]
            if refs.size == 0 or dets.size == 0:
                continue
            if refs.size == 1 and dets.size == 1:
                if abs(int(detected[dets[0]]) - int(reference[refs[0]])) <= tolerance:
                    pairs.append((int(refs[0]), int(dets[0])))
                continue
            cost = np.abs(detected[dets][None, :] - reference[refs][:, None]).astype(np.float64)
            forbidden = (min(refs.size, dets.size) + 1) * (tolerance + 1.0)
            cost = np.where(cost <= tolerance, cost, forbidden)
            rows, cols = linear_sum_assignment(cost)
            for row, col in zip(rows, cols, strict=True):
                if cost[row, col] <= tolerance:
                    pairs.append((int(refs[row]), int(dets[col])))
    pairs.sort()
    pair_reference = np.array([r for r, _ in pairs], dtype=np.int64)
    pair_detected = np.array([d for _, d in pairs], dtype=np.int64)
    reference_matched[pair_reference] = True
    detected_matched[pair_detected] = True
    return Match(
        reference_matched=reference_matched,
        detected_matched=detected_matched,
        pair_reference=pair_reference,
        offsets=detected[pair_detected] - reference[pair_reference],
    )


def score(
    reference: np.ndarray,
    detected: np.ndarray,
    region: np.ndarray,
    tolerance_ms: float,
    rate: int,
) -> dict[str, Any]:
    """TP / FN / FP dentro de `region` (máscara por muestra) y el desvío de los pares.

    Se empareja sobre los trenes **enteros** y recién después se cuenta dentro de la
    región: un latido al borde de una ventana GOOD cuya detección cayó 10 ms afuera
    no es un FN. TP y FN se cuentan por la posición de la referencia, FP por la de
    la detección. `shifted` son los TP a más de `FIDUCIAL_MS` de la referencia.
    """
    reference = np.asarray(reference, dtype=np.int64)
    detected = np.asarray(detected, dtype=np.int64)
    match = match_beats(reference, detected, tolerance_ms * rate / 1000.0)
    reference_in = region[reference]
    detected_in = region[detected]
    offsets_ms = match.offsets[reference_in[match.pair_reference]] * 1000.0 / rate
    return {
        "tp": int((match.reference_matched & reference_in).sum()),
        "fn": int((~match.reference_matched & reference_in).sum()),
        "fp": int((~match.detected_matched & detected_in).sum()),
        "shifted": int((np.abs(offsets_ms) > FIDUCIAL_MS).sum()),
        "offset_ms": _percentiles(offsets_ms),
    }


def _percentiles(values: np.ndarray) -> list[float]:
    """Percentiles 10 / 50 / 90 del desvío, o vacío si no hay pares."""
    if values.size == 0:
        return []
    return [float(v) for v in np.percentile(values, (10, 50, 90))]


def score_all(
    reference: np.ndarray,
    detections: dict[str, np.ndarray],
    regions: dict[str, np.ndarray],
    rate: int,
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """`score` para cada región × detector × tolerancia: `[región][detector][tol]`."""
    return {
        scope: {
            name: {f"{tol:g}": score(reference, peaks, region, tol, rate) for tol in TOLERANCES_MS}
            for name, peaks in detections.items()
        }
        for scope, region in regions.items()
    }


def edge_region(n_samples: int, rate: int) -> np.ndarray:
    region = np.zeros(n_samples, dtype=bool)
    edge = int(EDGE_S * rate)
    region[edge : max(n_samples - edge, edge)] = True
    return region


def ec57_region(n_samples: int, rate: int) -> np.ndarray:
    """Lo que compara `bxb` sin opciones: desde los 5 min hasta el final del registro."""
    region = np.zeros(n_samples, dtype=bool)
    region[int(EC57_START_S * rate) :] = True
    return region


# --------------------------------------------------------------------------- #
# Diagnóstico: el modo de falla de un registro, en una línea
# --------------------------------------------------------------------------- #


def diagnose(
    signal: np.ndarray,
    reference: np.ndarray,
    symbols: np.ndarray,
    detected: np.ndarray,
    region: np.ndarray,
    rate: int,
    tolerance_ms: float,
) -> dict[str, Any]:
    """Mide los errores a `tolerance_ms` lo suficiente como para nombrar el modo de falla.

    FP, según dónde cae la detección sobrante: `qrs` (a menos de 150 ms de un latido:
    doble disparo o fiducial corrido), `onda_t` (150-450 ms después de un latido) u
    `otro` (más lejos: onda P bloqueada, ruido, artefacto); y cuántos ms después del
    latido anterior caen, en mediana. FN: el símbolo del latido perdido, la detección
    más cercana (si está a menos de 300 ms, el latido no se perdió: se marcó corrido)
    y la amplitud pico a pico del QRS perdido contra la de los detectados. Los dos
    tipos de error, además, por minuto: un detector que se queda ciego pierde tramos
    enteros, uno que dispara en las T falla parejo.
    """
    match = match_beats(reference, detected, tolerance_ms * rate / 1000.0)
    ms = 1000.0 / rate
    missed = ~match.reference_matched & region[reference]
    hit = match.reference_matched & region[reference]
    extra = detected[~match.detected_matched & region[detected]]

    fp_kind: collections.Counter[str] = collections.Counter()
    after_ms: list[float] = []
    for peak in extra:
        position = int(np.searchsorted(reference, peak))
        previous = (peak - reference[position - 1]) * ms if position > 0 else math.inf
        following = (reference[position] - peak) * ms if position < reference.size else math.inf
        if math.isfinite(previous):
            after_ms.append(float(previous))
        if min(previous, following) <= NEAR_MS:
            fp_kind["qrs"] += 1
        elif T_WAVE_MS[0] < previous <= T_WAVE_MS[1]:
            fp_kind["onda_t"] += 1
        else:
            fp_kind["otro"] += 1

    nearest_ms: list[float] = []
    if detected.size:
        for peak in reference[missed]:
            position = int(np.searchsorted(detected, peak))
            candidates = detected[max(position - 1, 0) : position + 1] - peak
            nearest_ms.append(float(candidates[np.argmin(np.abs(candidates))] * ms))
    shifted = [value for value in nearest_ms if abs(value) <= SHIFTED_MS]

    half = int(AMPLITUDE_HALF_MS * rate / 1000.0)

    def amplitude(peaks: np.ndarray) -> float:
        if peaks.size == 0:
            return float("nan")
        return float(np.median([np.ptp(signal[max(p - half, 0) : p + half + 1]) for p in peaks]))

    amp_missed, amp_hit = amplitude(reference[missed]), amplitude(reference[hit])
    minute = 60 * rate
    minutes = int(signal.size // minute) + 1
    return {
        "fn_symbols": dict(collections.Counter(str(s) for s in symbols[missed]).most_common()),
        "fn_shifted": len(shifted),
        "fn_shift_ms": float(np.median(shifted)) if shifted else float("nan"),
        "fn_amplitude_ratio": amp_missed / amp_hit if amp_hit > 0 else float("nan"),
        "fn_per_minute": np.bincount(reference[missed] // minute, minlength=minutes).tolist(),
        "beats_per_minute": np.bincount(reference[region[reference]] // minute, minlength=minutes)
        .astype(int)
        .tolist(),
        "fp_kind": dict(fp_kind),
        "fp_after_ms": float(np.median(after_ms)) if after_ms else float("nan"),
        "fp_per_minute": np.bincount(extra // minute, minlength=minutes).tolist(),
    }


SYMBOL_TEXT = {
    "N": "normales",
    "V": "V",
    "/": "estimulados",
    "f": "fusión de MP",
    "A": "A",
    "L": "BRI",
    "R": "BRD",
    "F": "fusión",
    "Q": "inclasificables",
}


def busy_minutes(errors: list[int], beats: list[int], share: float = 0.3) -> str:
    """Los minutos donde el error pasa `share` de los latidos, como rangos `5-12, 16`.

    Vacío si son casi todos: entonces el error es parejo y no hay tramo que nombrar.
    """
    flagged = [
        m for m, (e, b) in enumerate(zip(errors, beats, strict=False)) if b and e >= share * b
    ]
    if not flagged or len(flagged) >= 0.8 * sum(1 for b in beats if b):
        return ""
    ranges: list[list[int]] = []
    for m in flagged:
        if ranges and m == ranges[-1][1] + 1:
            ranges[-1][1] = m
        else:
            ranges.append([m, m])
    return "min " + ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in ranges)


def failure_mode(record: str, totals: dict[str, Any], diag: dict[str, Any]) -> str:
    """Una línea con el error dominante. Es automática: DETECTORS.md la revisa a mano."""
    fn, fp = int(totals["fn"]), int(totals["fp"])
    notes: list[str] = ["marcapasos"] if record in PACED else []
    symbols = ", ".join(
        f"{SYMBOL_TEXT.get(s, s)} {c}" for s, c in list(diag["fn_symbols"].items())[:2] if c
    )
    if fn and diag["fn_shifted"] >= 0.7 * fn and fp >= 0.7 * fn:
        # Cada FN tiene su FP al lado: no se perdió el latido, se marcó en otro punto.
        notes.append(
            f"R corrido {diag['fn_shift_ms']:+.0f} ms de la anotación"
            f" ({diag['fn_shifted']}/{fn} FN con su FP al lado; {symbols})"
        )
        where = busy_minutes(diag["fn_per_minute"], diag["beats_per_minute"])
    elif fp > fn:
        kinds = diag["fp_kind"]
        kind = max(kinds, key=kinds.get) if kinds else "otro"
        text = {
            "qrs": "doble disparo sobre el mismo QRS",
            "onda_t": f"detecta la onda T (~{diag['fp_after_ms']:.0f} ms después del R)",
            "otro": f"FP lejos de todo QRS (~{diag['fp_after_ms']:.0f} ms después del R)",
        }[kind]
        notes.append(f"{text}: {kinds.get(kind, 0)}/{fp} FP")
        where = busy_minutes(diag["fp_per_minute"], diag["beats_per_minute"])
    elif fn:
        notes.append(f"pierde latidos sin detección cerca ({symbols})")
        ratio = diag["fn_amplitude_ratio"]
        if math.isfinite(ratio) and ratio < 0.7:
            notes.append(f"QRS perdidos de {ratio:.2f}× la amplitud de los detectados")
        where = busy_minutes(diag["fn_per_minute"], diag["beats_per_minute"])
    else:
        where = ""
    if where:
        notes.append(where)
    return "; ".join(notes) or "—"


# --------------------------------------------------------------------------- #
# Bancos de prueba
# --------------------------------------------------------------------------- #


def load_reference(name: str, base: str) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
    """Latidos anotados (muestras a 500 Hz y símbolos) y los tramos de flutter ventricular.

    La conversión de muestras es la de `evaluate.load_record`: `sample * 500 // 360`.
    """
    import wfdb

    annotation = wfdb.rdann(str(DATA_DIR / base / name), "atr")
    symbols = np.array(annotation.symbol)
    samples = np.asarray(annotation.sample, dtype=np.int64) * TARGET_RATE // SOURCE_RATE
    is_beat = np.isin(symbols, list(BEAT_SYMBOLS))
    flutter: list[tuple[int, int]] = []
    start: int | None = None
    for symbol, sample in zip(symbols, samples, strict=True):
        if symbol == VF_START and start is None:
            start = int(sample)
        elif symbol == VF_END and start is not None:
            flutter.append((start, int(sample)))
            start = None
    if start is not None:
        flutter.append((start, int(samples[-1]) + 1))
    return samples[is_beat], symbols[is_beat], flutter


def run_mitdb(name: str) -> dict[str, Any]:
    record = load_record(name)
    signal = record.signal_mv
    reference, symbols, flutter = load_reference(name, "mitdb")
    regions = {
        "todo": edge_region(signal.size, TARGET_RATE),
        "ec57": ec57_region(signal.size, TARGET_RATE),
    }
    for region in regions.values():
        for low, high in flutter:
            region[low:high] = False
    # Las referencias fuera de la señal (la última anotación puede caer en la muestra
    # final al redondear) se descartan para poder indexar la máscara.
    keep = reference < signal.size
    reference, symbols = reference[keep], symbols[keep]
    detectors = {**DETECTORS, **VARIANTS}
    detections = {key: detector(signal, TARGET_RATE) for key, detector in detectors.items()}
    scores = score_all(reference, detections, regions, TARGET_RATE)
    return {
        "part": "mitdb",
        "record": name,
        "duration_s": signal.size / TARGET_RATE,
        "excluded_flutter_s": sum(high - low for low, high in flutter) / TARGET_RATE,
        "scores": {scope: {"atr": by_detector} for scope, by_detector in scores.items()},
        "diag": {
            key: {
                scope: diagnose(
                    signal,
                    reference,
                    symbols,
                    detections[key],
                    regions[scope],
                    TARGET_RATE,
                    float(tol),
                )
                for scope, tol in WORST_CRITERIA
            }
            for key in DETECTORS
        },
    }


def run_nstdb(name: str, base: str) -> dict[str, Any]:
    """Un registro de `nstdb` (o su original limpio de `mitdb`), en bloques con y sin ruido.

    Al original limpio se le aplican los **mismos** bloques: así cada fila compara la
    misma porción de los mismos latidos, y lo único que cambia entre filas es el ruido.
    """
    record = load_record(name, base=base)
    signal = record.signal_mv
    reference, _, _ = load_reference(name, base)
    reference = reference[reference < signal.size]
    region = edge_region(signal.size, TARGET_RATE)
    noise = noisy_mask(signal.size)
    detectors = {**DETECTORS, **VARIANTS}
    detections = {key: detector(signal, TARGET_RATE) for key, detector in detectors.items()}
    scores = score_all(
        reference,
        detections,
        {"ruido": region & noise, "limpio": region & ~noise},
        TARGET_RATE,
    )
    return {
        "part": "nstdb",
        "record": name,
        "duration_s": signal.size / TARGET_RATE,
        "scores": {scope: {"atr": by_detector} for scope, by_detector in scores.items()},
    }


def run_vest(path: str) -> dict[str, Any]:
    from tools.vest import detectors as vest

    return vest.score_capture(Path(path))


def _complete(name: str, base: str) -> bool:
    return all((DATA_DIR / base / f"{name}{ext}").exists() for ext in (".hea", ".dat", ".atr"))


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("nan")


def fmt(value: float, digits: int = 4) -> str:
    return "—" if not math.isfinite(value) else f"{value:.{digits}f}"


def se_ppv(counts: dict[str, Any]) -> tuple[float, float]:
    tp, fn, fp = counts["tp"], counts["fn"], counts["fp"]
    return _ratio(tp, tp + fn), _ratio(tp, tp + fp)


def add(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    total = {"tp": 0, "fn": 0, "fp": 0, "shifted": 0}
    for item in items:
        for key in total:
            total[key] += int(item.get(key, 0))
    return total


def fmt_pair(counts: dict[str, Any]) -> str:
    se, ppv = se_ppv(counts)
    return f"{fmt(se, 3)} / {fmt(ppv, 3)}" if counts["tp"] + counts["fn"] else "—"


def fmt_offset(counts: dict[str, Any]) -> str:
    values = counts.get("offset_ms") or []
    return f"{values[1]:+.0f} ({values[0]:+.0f}…{values[2]:+.0f})" if values else "—"


def head(*columns: str) -> list[str]:
    """Encabezado de tabla markdown. La primera columna y las que empiezan con `<` van a
    la izquierda (el `<` no se imprime); las demás, números, a la derecha."""
    names = [column.removeprefix("<") for column in columns]
    align = ["---" if i == 0 or c.startswith("<") else "--:" for i, c in enumerate(columns)]
    return [row(names), row(align)]


def row(cells: Iterable[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _der(counts: dict[str, Any]) -> float:
    return _ratio(counts["fn"] + counts["fp"], counts["tp"] + counts["fn"])


#: Subconjuntos de MIT-BIH para los totales.
SUBSETS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("todos", lambda name: True),
    ("sin marcapasos", lambda name: name not in PACED),
    ("30 del sondeo previo", lambda name: name in PROBE_RECORDS),
)


def report_mitdb(results: list[dict[str, Any]]) -> list[str]:
    rows = sorted(results, key=lambda item: item["record"])
    out = ["## MIT-BIH Arrhythmia", ""]
    missing = [name for name in MITDB_RECORDS if not _complete(name, "mitdb")]
    out.append(
        f"{len(rows)} registros"
        + (f" (faltan {', '.join(missing)})" if missing else "")
        + f"; {sum(item['excluded_flutter_s'] for item in rows):.0f} s de flutter excluidos."
        " `corridos`: latidos detectados a ±150 ms pero a más de 50 ms de la anotación"
        " (error de ubicación del fiducial, no de detección)."
    )
    out += ["", "### Por registro, EC57 (desde 5 min, ±150 ms)", ""]
    out += head(
        *("registro", "latidos", "nk Se", "nk PPV", "nk FN", "nk FP", "nk corridos"),
        *("pt Se", "pt PPV", "pt FN", "pt FP", "pt corridos"),
    )
    for item in rows:
        scores = item["scores"]["ec57"]["atr"]
        nk, pt = scores["nk"]["150"], scores["pt"]["150"]
        cells = [
            item["record"] + ("ᵖ" if item["record"] in PACED else ""),
            str(nk["tp"] + nk["fn"]),
        ]
        for counts in (nk, pt):
            se, ppv = se_ppv(counts)
            cells += [fmt(se), fmt(ppv), str(counts["fn"]), str(counts["fp"])]
            cells.append(str(counts["shifted"]))
        out.append(row(cells))
    out += ["", "ᵖ con marcapasos.", "", "### Por registro, desde 1 s, ±75 ms", ""]
    out += head(
        *("registro", "latidos", "nk Se", "nk PPV", "nk FN", "nk FP"),
        *("pt Se", "pt PPV", "pt FN", "pt FP"),
    )
    for item in rows:
        scores = item["scores"]["todo"]["atr"]
        nk, pt = scores["nk"]["75"], scores["pt"]["75"]
        cells = [
            item["record"] + ("ᵖ" if item["record"] in PACED else ""),
            str(nk["tp"] + nk["fn"]),
        ]
        for counts in (nk, pt):
            se, ppv = se_ppv(counts)
            cells += [fmt(se), fmt(ppv), str(counts["fn"]), str(counts["fp"])]
        out.append(row(cells))
    out += ["", "### Por registro, desde 1 s, ±50 ms y desvío (detección − anotación)", ""]
    out += head(
        *("registro", "nk Se", "nk PPV", "pt Se", "pt PPV"),
        *("nk desvío ms p50 (p10…p90)", "pt desvío ms p50 (p10…p90)"),
    )
    for item in rows:
        scores = item["scores"]["todo"]["atr"]
        cells = [item["record"]]
        for key in ("nk", "pt"):
            se, ppv = se_ppv(scores[key]["50"])
            cells += [fmt(se), fmt(ppv)]
        cells += [fmt_offset(scores["nk"]["75"]), fmt_offset(scores["pt"]["75"])]
        out.append(row(cells))

    out += ["", "### Totales brutos (Σ TP / Σ FN / Σ FP sobre los registros)", ""]
    out.append(
        "`corridos` = Σ latidos corridos / latidos, solo a ±150 ms. EC57 es lo que hace `bxb`"
        " sin opciones; los demás criterios son más estrictos."
    )
    out += [""] + head(
        *("conjunto", "<criterio", "registros", "latidos"),
        *("nk Se", "nk PPV", "nk DER", "pt Se", "pt PPV", "pt DER", "nk corridos", "pt corridos"),
    )
    for label, keep in SUBSETS:
        chosen = [item for item in rows if keep(item["record"])]
        for name, scope, tol in CRITERIA:
            totals = {
                key: add(item["scores"][scope]["atr"][key][tol] for item in chosen)
                for key in DETECTORS
            }
            cells = [label, name, str(len(chosen)), str(totals["nk"]["tp"] + totals["nk"]["fn"])]
            for key in DETECTORS:
                se, ppv = se_ppv(totals[key])
                cells += [fmt(se), fmt(ppv), fmt(_der(totals[key]))]
            for key in DETECTORS:
                beats = totals[key]["tp"] + totals[key]["fn"]
                cells.append(fmt(_ratio(totals[key]["shifted"], beats)) if tol == "150" else "")
            out.append(row(cells))

    for scope, tol in WORST_CRITERIA:
        criterion = next(name for name, s, t in CRITERIA if (s, t) == (scope, tol))
        for key in DETECTORS:
            out += ["", f"### Los 5 peores de `{key}`, {criterion} (por DER = (FN + FP) / latidos)"]
            out += [""] + head(
                "registro", "Se", "PPV", "FN", "FP", "DER", "<modo de falla (automático)"
            )

            def der(
                item: dict[str, Any], key: str = key, scope: str = scope, tol: str = tol
            ) -> float:
                return _der(item["scores"][scope]["atr"][key][tol])

            for item in sorted(rows, key=der, reverse=True)[:5]:
                counts = item["scores"][scope]["atr"][key][tol]
                se, ppv = se_ppv(counts)
                mode = failure_mode(item["record"], counts, item["diag"][key][scope])
                out.append(
                    f"| {item['record']} | {fmt(se)} | {fmt(ppv)} | {counts['fn']} | {counts['fp']}"
                    f" | {fmt(der(item))} | {mode} |"
                )
    return out + [""] + report_variants_mitdb(rows)


def _base(key: str) -> str:
    """El detector de producción del que sale una variante: `pt_por_lote` → `pt`."""
    return key.split("_", 1)[0]


#: Columnas de las tablas de variantes: cada detector, seguido de sus variantes.
VARIANT_COLUMNS = tuple(
    key for base in DETECTORS for key in (base, *VARIANTS) if _base(key) == base
)


def report_variants_mitdb(rows: list[dict[str, Any]]) -> list[str]:
    """Los dos detectores sobre el registro entero contra sus variantes (`VARIANTS`)."""
    out = ["### Variantes", ""]
    out += [f"- {text}" for text in VARIANT_LABELS.values()] + [""]
    out += head("conjunto", "<criterio", "latidos", *(f"{key} DER" for key in VARIANT_COLUMNS))
    for label, keep in SUBSETS[:2]:
        chosen = [item for item in rows if keep(item["record"])]
        for name, scope, tol in CRITERIA:
            cells = [label, name]
            for index, key in enumerate(VARIANT_COLUMNS):
                totals = add(item["scores"][scope]["atr"][key][tol] for item in chosen)
                if index == 0:
                    cells.append(str(totals["tp"] + totals["fn"]))
                cells.append(fmt(_der(totals)))
            out.append(row(cells))
    out += ["", "Se / PPV de las mismas filas:", ""]
    out += head("conjunto", "<criterio", *VARIANT_COLUMNS)
    for label, keep in SUBSETS[:2]:
        chosen = [item for item in rows if keep(item["record"])]
        for name, scope, tol in CRITERIA:
            cells = [label, name]
            for key in VARIANT_COLUMNS:
                cells.append(
                    fmt_pair(add(item["scores"][scope]["atr"][key][tol] for item in chosen))
                )
            out.append(row(cells))
    for key in VARIANTS:
        base = _base(key)
        changed = []
        for item in rows:
            scores = item["scores"]["ec57"]["atr"]
            before, after = _der(scores[base]["150"]), _der(scores[key]["150"])
            if abs(after - before) > 0.005:
                changed.append(f"{item['record']} {before:.3f} → {after:.3f}")
        out += [
            "",
            f"DER de `{base}` → `{key}` en EC57, donde cambia más de 0,005: "
            + ("; ".join(changed) if changed else "ninguno")
            + ".",
        ]
    return out


def report_nstdb(results: list[dict[str, Any]]) -> list[str]:
    def snr_of(name: str) -> str:
        return "limpio" if "e" not in name else name.split("e", 1)[1].replace("_", "−")

    order = {
        snr: index
        for index, snr in enumerate(("limpio", *(s.replace("_", "−") for s in NOISE_SNR)))
    }
    rows = sorted(results, key=lambda item: (item["record"][:3], order[snr_of(item["record"])]))
    out = ["## NSTDB (ruido de movimiento de electrodo, canal 0)", ""]
    out.append(
        "Se / PPV a ±150 ms, la ventana de EC57. `ruido` = los bloques de 2 min con ruido"
        " sumado (empiezan a los 5 min, así que caen en la región de EC57); `limpio` = el"
        " resto (los 5 min iniciales y los bloques alternos). La fila `limpio` es el registro"
        " original de `mitdb`, puntuado sobre los mismos bloques."
    )
    out += [""] + head("registro", "SNR dB", "ruido nk", "ruido pt", "limpio nk", "limpio pt")
    for item in rows:
        scores = item["scores"]
        cells = [item["record"], snr_of(item["record"])]
        cells += [
            fmt_pair(scores[scope]["atr"][key]["150"])
            for scope in ("ruido", "limpio")
            for key in DETECTORS
        ]
        out.append(row(cells))

    out += ["", "### Totales por SNR (118 + 119, Σ TP / FN / FP)", ""]
    out += head("SNR dB", "tol", "ruido nk", "ruido pt", "limpio nk", "limpio pt")
    by_snr: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for item in rows:
        by_snr[snr_of(item["record"])].append(item)
    snrs = sorted(by_snr, key=order.__getitem__)
    for snr in snrs:
        for tol in (f"{tol:g}" for tol in TOLERANCES_MS):
            cells = [snr, f"±{tol} ms"]
            for scope in ("ruido", "limpio"):
                for key in DETECTORS:
                    cells.append(
                        fmt_pair(add(i["scores"][scope]["atr"][key][tol] for i in by_snr[snr]))
                    )
            out.append(row(cells))

    out += ["", "### Variantes por SNR (±150 ms)", ""]
    keys = VARIANT_COLUMNS
    out += head("SNR dB", *(f"ruido {k}" for k in keys), *(f"limpio {k}" for k in keys))
    for snr in snrs:
        cells = [snr]
        for scope in ("ruido", "limpio"):
            for key in keys:
                cells.append(
                    fmt_pair(add(i["scores"][scope]["atr"][key]["150"] for i in by_snr[snr]))
                )
        out.append(row(cells))
    return out


def report(results: list[dict[str, Any]]) -> str:
    by_part: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for item in results:
        by_part[item["part"]].append(item)
    lines = [f"- {text}" for text in {**DETECTOR_LABELS, **VARIANT_LABELS}.values()] + [""]
    if by_part["mitdb"]:
        lines += report_mitdb(by_part["mitdb"]) + [""]
    if by_part["nstdb"]:
        lines += report_nstdb(by_part["nstdb"]) + [""]
    if by_part["vest"]:
        from tools.vest import detectors as vest

        lines += vest.report(by_part["vest"]) + [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def jobs_for(parts: Sequence[str], records: Sequence[str] | None, captures: Path) -> list[tuple]:
    jobs: list[tuple] = []
    if "mitdb" in parts:
        for name in records or MITDB_RECORDS:
            if _complete(name, "mitdb"):
                jobs.append(("mitdb", name))
            else:
                print(f"  mitdb {name}: incompleto, se saltea (--download)", flush=True)
    if "nstdb" in parts:
        for base in NSTDB_BASES:
            for name, folder in [(base, "mitdb")] + [
                (f"{base}e{snr}", "nstdb") for snr in NOISE_SNR
            ]:
                if _complete(name, folder):
                    jobs.append(("nstdb", name, folder))
                else:
                    print(f"  nstdb {name}: incompleto, se saltea (--download)", flush=True)
    if "vest" in parts:
        from tools.vest.evaluate import PATTERN

        jobs += [("vest", str(path)) for path in sorted(captures.glob(PATTERN))]
    return jobs


def run_job(job: tuple) -> dict[str, Any]:
    kind = job[0]
    if kind == "mitdb":
        return run_mitdb(job[1])
    if kind == "nstdb":
        return run_nstdb(job[1], job[2])
    return run_vest(job[1])


def main(argv: Sequence[str] | None = None) -> int:
    from tools.vest.evaluate import DEFAULT_CAPTURES

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--part", default="mitdb,nstdb,vest", help="mitdb,nstdb,vest (coma)")
    parser.add_argument("--records", nargs="*", default=None, help="subconjunto de mitdb")
    parser.add_argument("--captures-dir", type=Path, default=DEFAULT_CAPTURES)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--out", type=Path, default=RESULTS_PATH, help="JSON con los conteos")
    parser.add_argument("--download", action="store_true", help="bajar mitdb y nstdb y salir")
    parser.add_argument(
        "--summarize-only", action="store_true", help="rehacer el reporte desde --out"
    )
    args = parser.parse_args(argv)
    parts = [part.strip() for part in args.part.split(",") if part.strip()]

    if args.download:
        download(MITDB_RECORDS)
        for base in NSTDB_BASES:
            download_noise(base)
        return 0
    if args.summarize_only:
        results = json.loads(args.out.read_text())
        print(report([item for item in results if item["part"] in parts]))
        return 0

    jobs = jobs_for(parts, args.records, args.captures_dir)
    if not jobs:
        print("No hay nada que correr (¿faltan los datos? ver --download).")
        return 1
    started = time.time()
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(run_job, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            job = futures[future]
            try:
                results.append(future.result())
            except Exception as error:  # noqa: BLE001 — un registro roto no corta el resto
                print(f"  [{done}/{len(jobs)}] {job[1]}: FALLÓ {error!r}", flush=True)
                continue
            print(f"  [{done}/{len(jobs)}] {job[0]} {Path(job[1]).stem}", flush=True)
    print(f"  {len(results)} corridas en {time.time() - started:.0f} s\n", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1, default=float))
    print(report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
