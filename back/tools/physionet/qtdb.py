"""Benchmark de delineación (QRS / QT) contra la QT Database de PhysioNet. Ver el README.

    cd back
    uv run python -m tools.physionet.evaluate --qtdb --download
    uv run python -m tools.physionet.evaluate --qtdb --jobs 9
    uv run python -m tools.physionet.evaluate --qtdb --methods prominence,production --lead 0
    uv run python -m tools.physionet.evaluate --qtdb --variants      # + rb200 y Q→S
    uv run python -m tools.physionet.evaluate --qtdb --summarize-only

`python -m tools.physionet.qtdb ...` hace lo mismo sin importar el resto del motor.

Gate de la Fase 3 (plan): |bias| ≤ 20 ms en QRS y ≤ 30 ms en QT contra las marcas
manuales del primer cardiólogo (anotador `q1c`), por latido **y** por mediana de
registro. Definiciones del plan: QRS = R_onset → R_offset; QT = R_onset → T_offset.

Réplica de producción:

* 250 Hz → 500 Hz con `resample_poly(x, 2, 1)`; las marcas se multiplican por 2.
* `clean_signal` y `detect_rpeaks` de `app.ml.rpeak_detection`, los mismos de la ingesta.
* Bloques de 300 s como en producción: se limpia, se detecta y se delinea por bloque. El
  bloque se centra en el tramo anotado (las marcas de `q1c` caen casi siempre en un
  minuto alrededor de t = 600 s; con bloques alineados a 0 los latidos anotados quedarían
  pegados al borde, un artefacto que producción no tiene).

Dos fuentes de R por derivación:

* `detected`: lo que ve producción, `nk.ecg_peaks(correct_artifacts=False)`.
* `reference`: la regla de `nk.ecg_peaks` (máximo local más prominente) aplicada dentro del
  QRS manual ± 20 ms, el mismo fiducial que el detector (coincide ± 4 ms en el 97 % / 96 %
  de los latidos de L0 / L1). El resto del bloque sale del detector, y los huecos del tren
  se rellenan con la detección de la otra derivación (`fill_gaps`).

**Nada se ajusta sobre estas etiquetas**: solo se leen después, para medir.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ml.rpeak_detection import clean_signal, detect_rpeaks  # noqa: E402
from tools.physionet import qtdb_report as report  # noqa: E402
from tools.physionet.qtdb_delineation import (  # noqa: E402
    PRODUCTION,
    PRODUCTION_MASKS,
    PRODUCTION_VALUES,
    STOCK_METHODS,
    TARGET_RATE,
    VARIANT_METHODS,
    WAVE_KEYS,
    block_stats,
    delineate_aligned,
    known_method,
    mains_hz_for,
    measure_production,
    production_status,
    production_thresholds,
)

DATA_DIR = Path(__file__).parent / "data" / "qtdb"
RESULTS_DIR = Path(__file__).parent / "data" / "qtdb_results"

SOURCE_RATE = 250
UPSAMPLE = TARGET_RATE // SOURCE_RATE
BLOCK_SECONDS = 300.0
#: R de referencia: regla de `nk.ecg_peaks` dentro de [onset − pad, offset + pad] del QRS
#: manual. 20 ms se eligió por acuerdo con el detector (0, 10 y 40 ms dan 97,0–97,3 % /
#: 93,3–95,7 %), nunca por el error contra el gate.
SNAP_QRS_PAD_MS = 20.0
#: R insertados desde la otra derivación: regla de `nk.ecg_peaks` en ± 50 ms de esa detección.
GAP_SNAP_MS = 50.0
#: Contexto del tren de referencia: detectados sin pareja a > 200 ms de un R anotado. Más
#: cerca son una doble detección (o la T), y dos R a < 90 ms hacen reventar a `cwt`.
REFRACTORY_MS = 200.0
#: Relleno de huecos: un intervalo del tren > GAP_FACTOR × RR mediano se rellena con
#: detecciones de la otra derivación separadas ≥ GAP_MIN_SEP × RR de todo.
GAP_FACTOR = 1.5
GAP_MIN_SEP = 0.6
MATCH_MS = 75.0  # R detectado <-> latido anotado
#: Códigos de latido WFDB: en `q1c` el pico de QRS lleva la etiqueta del latido.
BEAT_SYMBOLS = frozenset("NLRBAaJSVrFejnE/fQ?")
DEFAULT_METHODS = (*STOCK_METHODS, PRODUCTION)
#: Columnas por latido de `production` (ver `measure_production`) cuando no hay medición.
PRODUCTION_DEFAULTS: dict[str, Any] = {
    **{f"prod_{k}": False for k in PRODUCTION_MASKS},
    **{f"prod_{k}": np.nan for k in PRODUCTION_VALUES},
}
#: Orden de `beats.csv`: con él la salida es la misma bit a bit entre corridas (los
#: registros terminan en cualquier orden y las sumas en float dependen del orden).
BEAT_ORDER = ["record", "lead", "rpeaks", "method", "beat"]
#: Código de salida cuando `production` estaba pedido y no corrió entero.
EXIT_PRODUCTION_FAILED = 3


# --------------------------------------------------------------------------- datos
def download(records: list[str] | None = None) -> int:
    """Bajada idempotente archivo por archivo, con reintentos ante 502 (PhysioNet los da)."""
    import wfdb

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    names = records or wfdb.get_record_list("qtdb")
    pending = [
        f"{r}{e}"
        for r in names
        for e in (".hea", ".dat", ".q1c")
        if not (DATA_DIR / f"{r}{e}").exists()
    ]
    print(f"qtdb: {len(names)} registros, {len(pending)} archivos pendientes -> {DATA_DIR}")
    failed = []
    for name in pending:
        for attempt in range(5):
            try:
                wfdb.dl_files("qtdb", str(DATA_DIR), [name])
                break
            except Exception as error:  # noqa: BLE001 — PhysioNet corta con 502 seguido
                if "404" in str(error):
                    print(f"  404 {name}")
                    break
                time.sleep(2 * (attempt + 1))
        else:
            failed.append(name)
            print(f"  FALLÓ {name}")
    print(f"  fallidos: {failed}" if failed else "  ok")
    return 1 if failed else 0


def available_records() -> list[str]:
    return sorted(
        p.stem
        for p in DATA_DIR.glob("*.q1c")
        if (DATA_DIR / f"{p.stem}.hea").exists() and (DATA_DIR / f"{p.stem}.dat").exists()
    )


def parse_q1c(record: str) -> pd.DataFrame:
    """Latidos con QRS onset / pico / offset y fin de T, en muestras de 250 Hz.

    El pico de QRS en `q1c` lleva la etiqueta del latido: casi siempre `N`, pero hay
    registros enteros con `A` (sel232) o `B` (sel36), y latidos `V`/`Q` intercalados (sel44,
    sel50, sel37). Se toma cualquier símbolo de latido WFDB como pico y se guarda la
    etiqueta en `beat_type`: si solo se buscara `N`, la `t` de un latido `V` se le
    asignaría al `N` anterior y el QT saldría de cientos de segundos.

    `(` inmediatamente antes del pico = QRS onset; `)` inmediatamente después = QRS offset;
    fin de T = `)` inmediatamente después de la última `t` antes del latido siguiente. Un
    latido es utilizable si tiene las tres marcas y onset < pico < offset < fin de T.
    """
    import wfdb

    ann = wfdb.rdann(str(DATA_DIR / record), "q1c")
    sym = list(ann.symbol)
    smp = np.asarray(ann.sample, dtype=np.int64)
    beat_idx = [i for i, s in enumerate(sym) if s in BEAT_SYMBOLS]
    rows = []
    for k, i in enumerate(beat_idx):
        nxt = beat_idx[k + 1] if k + 1 < len(beat_idx) else len(sym)
        on = smp[i - 1] if i > 0 and sym[i - 1] == "(" else None
        off = smp[i + 1] if i + 1 < len(sym) and sym[i + 1] == ")" else None
        t_marks = [j for j in range(i + 1, nxt) if sym[j] == "t"]
        t_peak = t_off = None
        if t_marks:
            j = t_marks[-1]
            t_peak = smp[j]
            if j + 1 < len(sym) and sym[j + 1] == ")":
                t_off = smp[j + 1]
        rows.append(
            {
                "record": record,
                "beat": k,
                "r_ann": int(smp[i]),
                "beat_type": sym[i],
                "qrs_on": None if on is None else int(on),
                "qrs_off": None if off is None else int(off),
                "t_peak": None if t_peak is None else int(t_peak),
                "t_off": None if t_off is None else int(t_off),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ("qrs_on", "qrs_off", "t_peak", "t_off"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    complete = df[["qrs_on", "qrs_off", "t_off"]].notna().all(axis=1)
    df["usable"] = (
        complete
        & (df["qrs_on"] < df["r_ann"])
        & (df["r_ann"] < df["qrs_off"])
        & (df["qrs_off"] < df["t_off"])
    )
    return df


# --------------------------------------------------------------------------- R de referencia
def nk_rule_peak(cleaned: np.ndarray, lo: int, hi: int) -> tuple[int, bool]:
    """Regla de `_ecg_findpeaks_neurokit` (NeuroKit 0.2.13) en `cleaned[lo:hi]`.

    El máximo local más prominente (`find_peaks(seg, prominence=(None, None))`, con las
    prominencias calculadas dentro del segmento). Sin máximo local -> argmax del segmento,
    marcado como fallback.
    """
    from scipy.signal import find_peaks

    lo, hi = max(0, int(lo)), min(cleaned.size, int(hi))
    if hi <= lo:
        return max(0, min(cleaned.size - 1, lo)), True
    seg = cleaned[lo:hi]
    peaks, props = find_peaks(seg, prominence=(None, None))
    if peaks.size:
        return lo + int(peaks[int(np.argmax(props["prominences"]))]), False
    return lo + int(np.argmax(seg)), True


def reference_peaks(
    cleaned: np.ndarray, onsets: np.ndarray, offsets: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """R de referencia de cada latido anotado: `nk_rule_peak` dentro del QRS manual ± pad.

    Es el punto que elegiría `nk.ecg_peaks`, que busca el máximo local más prominente dentro
    de la región QRS que delimita su gradiente suavizado. Los delineadores de NeuroKit
    asumen ese R positivo (`prominence` va al máximo en ± 20 ms; Q y S se buscan a izquierda
    y derecha). El argmax|·| en ± 30 ms de la marca, que se usó antes, caía en S/QS en el
    22 % de los latidos de L0 y el 53 % de L1: era otro fiducial que el de producción.
    """
    pad = int(round(SNAP_QRS_PAD_MS * TARGET_RATE / 1000))
    out = np.empty(onsets.size, dtype=np.int64)
    fallback = np.zeros(onsets.size, dtype=bool)
    for k, (on, off) in enumerate(zip(onsets, offsets, strict=True)):
        out[k], fallback[k] = nk_rule_peak(cleaned, int(on) - pad, int(off) + pad + 1)
    return out, fallback


def fill_gaps(
    train: np.ndarray, extra: np.ndarray, n_samples: int, cleaned: np.ndarray
) -> tuple[np.ndarray, int, int]:
    """Rellena huecos del tren de referencia con detecciones de la otra derivación.

    Si el detector pierde latidos en la derivación evaluada (sel31 L1: 18 R en 300 s), el
    tren queda con huecos de decenas de segundos y los delineadores que miran el latido
    vecino o la FC media (`prominence` segmenta hasta R + RR/2, `peak` escala ventanas con
    la FC media) ponen el T_off a segundos del R. Solo para el tren `reference` (oráculo);
    `detected` es lo que ve producción y no se toca. Los huecos que quedan son pausas reales
    (sel232) o FA (sel221). Devuelve (tren, insertados, huecos residuales).
    """
    if train.size < 3:
        return train, 0, 0
    med = float(np.median(np.diff(train)))
    if med <= 0:
        return train, 0, 0
    sep = GAP_MIN_SEP * med
    gaps = [(float(a), float(b)) for a, b in zip(train[:-1], train[1:]) if b - a > GAP_FACTOR * med]
    if train[0] > GAP_FACTOR * med:
        gaps.append((-np.inf, float(train[0])))
    if n_samples - train[-1] > GAP_FACTOR * med:
        gaps.append((float(train[-1]), np.inf))
    if not gaps or extra.size == 0:
        return train, 0, sum(1 for a, b in gaps if math.isfinite(a) and math.isfinite(b))
    w = int(round(GAP_SNAP_MS * TARGET_RATE / 1000))
    snapped = np.array(
        [nk_rule_peak(cleaned, int(p) - w, int(p) + w + 1)[0] for p in extra], dtype=np.int64
    )
    added: list[int] = []
    for a, b in gaps:
        last = a
        for c in np.sort(snapped[(snapped > a + sep) & (snapped < b - sep)]):
            if c - last >= sep:
                added.append(int(c))
                last = c
    out = np.unique(np.concatenate([train, np.asarray(added, dtype=np.int64)]))
    return out, len(added), int((np.diff(out) > GAP_FACTOR * med).sum())


def nearest_within(sorted_peaks: np.ndarray, targets: np.ndarray, tol: int) -> np.ndarray:
    """Índice en `sorted_peaks` del pico más cercano a cada target, -1 si está a más de `tol`."""
    out = np.full(targets.size, -1, dtype=np.int64)
    if sorted_peaks.size == 0:
        return out
    pos = np.searchsorted(sorted_peaks, targets)
    for k, (t, p) in enumerate(zip(targets, pos, strict=True)):
        best, best_d = -1, tol + 1
        for c in (p - 1, p):
            if 0 <= c < sorted_peaks.size:
                d = abs(int(sorted_peaks[c]) - int(t))
                if d < best_d:
                    best, best_d = c, d
        out[k] = best if best_d <= tol else -1
    return out


def block_bounds(n_samples: int, ann_lo: int, ann_hi: int) -> tuple[int, int]:
    """Bloque de 300 s (producción) centrado en el tramo anotado."""
    block = int(BLOCK_SECONDS * TARGET_RATE)
    if n_samples <= block:
        return 0, n_samples
    center = (ann_lo + ann_hi) // 2
    start = max(0, min(center - block // 2, n_samples - block))
    return start, start + block


def highpass(x: np.ndarray) -> np.ndarray:
    """Solo la primera etapa de `nk.ecg_clean`: Butterworth pasaaltos de 0,5 Hz, orden 5.

    Sin la segunda (`powerline`: media móvil de 10 muestras ida y vuelta, un pasabajos de
    ~16 Hz que atenúa el R ~19 %). Es la señal "sin filtrar pero sin línea de base" sobre
    la que se mide la amplitud R alternativa.
    """
    import neurokit2 as nk

    finite = np.nan_to_num(x.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    filtered = nk.signal_filter(
        finite, sampling_rate=TARGET_RATE, lowcut=0.5, method="butterworth", order=5
    )
    return np.asarray(filtered, dtype=np.float64)


# --------------------------------------------------------------------------- un registro
@dataclass(frozen=True)
class Job:
    record: str
    leads: tuple[int, ...]
    methods: tuple[str, ...]
    sources: tuple[str, ...]
    cwt_cache: bool = True
    fill_gaps: bool = True
    next_r_guard: bool = True
    #: Campos de `IntervalThresholds` que cambian respecto del default del módulo, como
    #: pares (hashable para un dataclass congelado que viaja a otro proceso).
    production_overrides: tuple[tuple[str, Any], ...] = ()


def _prepare_lead(signal: np.ndarray, ann_lo: int, ann_hi: int) -> dict[str, Any]:
    from scipy.signal import resample_poly

    x = resample_poly(np.asarray(signal, dtype=np.float64), UPSAMPLE, 1)
    start, end = block_bounds(x.size, ann_lo, ann_hi)
    cleaned = clean_signal(x[start:end], TARGET_RATE)
    return {
        "x": x,
        "start": start,
        "end": end,
        "cleaned": cleaned,
        "hp": highpass(x[start:end]),
        "detected": detect_rpeaks(cleaned, TARGET_RATE),
    }


def _reference_train(detected: np.ndarray, det_match: np.ndarray, ref_in: np.ndarray) -> np.ndarray:
    """R de referencia de los latidos anotados + el resto del bloque desde el detector.

    El contexto hace falta porque los delineadores miran el latido vecino (`cwt` busca T/P
    entre R_i y R_i+1, `dwt` toma la FC mediana). Se descartan los detectados que ya tienen
    pareja anotada y los que caen a < `REFRACTORY_MS` de un R de referencia.
    """
    matched = {int(m) for m in det_match if m >= 0}
    refractory = int(REFRACTORY_MS * TARGET_RATE / 1000)
    context = np.array(
        [
            p
            for k, p in enumerate(detected)
            if k not in matched and (ref_in.size == 0 or np.min(np.abs(ref_in - p)) > refractory)
        ],
        dtype=np.int64,
    )
    return np.unique(np.concatenate([ref_in, context]))


def _delineate(
    method: str, job: Job, lead_data: dict[str, Any], train: np.ndarray
) -> tuple[dict[str, np.ndarray], int, dict[str, Any], dict[str, np.ndarray]]:
    """(marcas por latido, claves desalineadas, medianas de bloque, columnas de producción)."""
    cleaned, hp = lead_data["cleaned"], lead_data["hp"]
    if method == PRODUCTION:
        per_beat, stats = measure_production(
            cleaned,
            hp,
            train,
            mains_hz=mains_hz_for(job.record),
            overrides=dict(job.production_overrides),
        )
        return {}, 0, stats, per_beat
    waves, misaligned = delineate_aligned(cleaned, train, method, cwt_cache=job.cwt_cache)
    stats = block_stats(waves, train, cleaned, hp, next_r_guard=job.next_r_guard)
    return waves, misaligned, stats, {}


def process_record(job: Job) -> dict[str, Any]:
    import wfdb

    warnings.filterwarnings("ignore")
    t0 = time.time()
    rec = wfdb.rdrecord(str(DATA_DIR / job.record), physical=True)
    if rec.fs != SOURCE_RATE:
        raise RuntimeError(f"{job.record}: fs = {rec.fs}")
    ann = parse_q1c(job.record)
    meta: dict[str, Any] = {
        "record": job.record,
        "sig_name": list(rec.sig_name),
        "n_q1c_beats": int(len(ann)),
        "n_usable_beats": int(ann["usable"].sum()) if len(ann) else 0,
        "errors": [],
        "misaligned_keys": {},
        "seconds": {},
        "block": {},
        "gaps": {},
        "n_detected_matched": {},
    }
    if meta["n_usable_beats"] == 0:
        return {"meta": meta, "beats": []}
    ann = ann[ann["usable"]].reset_index(drop=True)
    r_ann = ann["r_ann"].to_numpy(np.int64) * UPSAMPLE
    man_on = ann["qrs_on"].to_numpy(np.int64) * UPSAMPLE
    man_off = ann["qrs_off"].to_numpy(np.int64) * UPSAMPLE
    man_toff = ann["t_off"].to_numpy(np.int64) * UPSAMPLE
    man_tpeak = ann["t_peak"].to_numpy(np.float64) * UPSAMPLE
    # Las dos derivaciones primero: el tren de referencia de una rellena sus huecos con la
    # detección de la otra.
    n_leads = rec.p_signal.shape[1]
    prep = {
        lead: _prepare_lead(rec.p_signal[:, lead], int(r_ann.min()), int(r_ann.max()))
        for lead in range(n_leads)
    }
    rows: list[dict[str, Any]] = []
    for lead in (lead for lead in job.leads if lead < n_leads):
        data = prep[lead]
        x, start, cleaned, detected = data["x"], data["start"], data["cleaned"], data["detected"]
        inside = (r_ann >= start) & (r_ann < data["end"])
        ref_local, snap_fallback = reference_peaks(cleaned, man_on - start, man_off - start)
        det_match = nearest_within(detected, ref_local, int(MATCH_MS * TARGET_RATE / 1000))
        meta["n_detected_matched"][f"L{lead}"] = int(((det_match >= 0) & inside).sum())

        trains: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        if "detected" in job.sources:
            trains["detected"] = (detected, np.where(inside, det_match, -1))
        if "reference" in job.sources:
            ref_in = ref_local[inside]
            train = _reference_train(detected, det_match, ref_in)
            if job.fill_gaps:
                others = [prep[o]["detected"] + prep[o]["start"] - start for o in prep if o != lead]
                extra = np.concatenate(others) if others else np.empty(0, dtype=np.int64)
                extra = extra[(extra >= 0) & (extra < cleaned.size)]
                train, n_added, n_residual = fill_gaps(train, extra, cleaned.size, cleaned)
                meta["gaps"][f"L{lead}"] = {"added": n_added, "residual": n_residual}
            idx = np.full(ref_local.size, -1, dtype=np.int64)
            idx[inside] = np.searchsorted(train, ref_in)
            trains["reference"] = (train, idx)

        for source, (train, idx) in trains.items():
            if train.size < 3:
                meta["errors"].append(f"L{lead}/{source}: tren de R con {train.size} picos")
                continue
            rr_prev = np.full(train.size, np.nan)
            rr_prev[1:] = np.diff(train) / TARGET_RATE
            r_next = np.full(train.size, np.nan)
            r_next[:-1] = train[1:]
            for method in job.methods:
                key = f"L{lead}/{source}/{method}"
                t1 = time.time()
                try:
                    waves, misaligned, stats, per_beat = _delineate(method, job, data, train)
                except Exception as error:  # noqa: BLE001 — guarda de crash, ver README
                    meta["errors"].append(f"{key}: {type(error).__name__}: {error}")
                    waves, misaligned, per_beat = {}, 0, {}
                    stats = block_stats({}, train, cleaned, data["hp"])
                meta["seconds"][key] = round(time.time() - t1, 2)
                meta["misaligned_keys"][key] = misaligned
                meta["block"][key] = stats
                for b in range(len(ann)):
                    i = int(idx[b])
                    r_ref = int(ref_local[b] + start)
                    row: dict[str, Any] = {
                        "record": job.record,
                        "lead": lead,
                        "lead_name": rec.sig_name[lead],
                        "rpeaks": source,
                        "method": method,
                        "beat": int(ann.at[b, "beat"]),
                        "beat_type": str(ann.at[b, "beat_type"]),
                        "man_on": int(man_on[b]),
                        "man_off": int(man_off[b]),
                        "man_toff": int(man_toff[b]),
                        "man_tpeak": float(man_tpeak[b]),
                        "r_ref": r_ref,
                        "snap_fallback": bool(snap_fallback[b]),
                        "r_used": np.nan,
                        "r_next": np.nan,
                        "rr_prev_s": np.nan,
                        "r_amp": np.nan,
                        "r_amp_hp": np.nan,
                        # Amplitud R de referencia sobre la señal SIN filtrar (remuestreada):
                        # x[R_ref] − x[onset manual]. En 50-100 ms la deriva de línea de base
                        # es despreciable.
                        "r_amp_ref": float(x[r_ref] - x[int(man_on[b])]),
                        **dict.fromkeys(WAVE_KEYS, np.nan),
                    }
                    if method == PRODUCTION:
                        row.update(PRODUCTION_DEFAULTS)
                    if i >= 0:
                        row["r_used"] = int(train[i] + start)
                        row["r_next"] = float(r_next[i] + start)
                        row["rr_prev_s"] = rr_prev[i]
                        for short, nk_key in WAVE_KEYS.items():
                            arr = waves.get(nk_key)
                            if arr is not None and math.isfinite(arr[i]):
                                row[short] = int(arr[i]) + start
                        if math.isfinite(row["r_on"]):
                            on_local = int(row["r_on"]) - start
                            row["r_amp"] = float(cleaned[int(train[i])] - cleaned[on_local])
                            row["r_amp_hp"] = float(
                                data["hp"][int(train[i])] - data["hp"][on_local]
                            )
                        for col, values in per_beat.items():
                            row[col] = values[i].item()
                    rows.append(row)
    meta["seconds"]["total"] = round(time.time() - t0, 2)
    return {"meta": meta, "beats": rows}


# --------------------------------------------------------------------------- CLI
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="evaluate --qtdb",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--download", action="store_true", help="bajar QTDB (hea/dat/q1c) y salir")
    p.add_argument(
        "--methods",
        default=",".join(DEFAULT_METHODS),
        help=f"coma-separados, de {', '.join((*DEFAULT_METHODS, *VARIANT_METHODS))}. "
        "`production` se omite con un aviso si app.ml.intervals no se puede importar",
    )
    p.add_argument(
        "--variants",
        action="store_true",
        help="agrega prominence~rb200 y la definición Q→S / Q→Toff a todos los métodos de stock",
    )
    p.add_argument("--lead", default="both", choices=["0", "1", "both"])
    p.add_argument("--rpeaks", default="both", choices=["reference", "detected", "both"])
    p.add_argument("--records", default="", help="subconjunto coma-separado (default: todos)")
    p.add_argument(
        "--record-min-beats",
        type=int,
        default=10,
        help="latidos válidos mínimos para que un registro entre a la métrica por mediana",
    )
    p.add_argument(
        "--min-block-beats",
        type=int,
        default=30,
        help=(
            "latidos válidos mínimos para que un bloque reporte mediana "
            "(`IntervalThresholds.min_beats`)"
        ),
    )
    p.add_argument("--boot", type=int, default=2000, help="remuestreos del bootstrap de registros")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--out", default=str(RESULTS_DIR))
    p.add_argument(
        "--summarize-only", action="store_true", help="re-agregar beats.csv sin re-delinear"
    )
    p.add_argument(
        "--no-cwt-cache", action="store_true", help="no memoizar pywt.cwt (mismo resultado)"
    )
    p.add_argument(
        "--no-fill-gaps",
        action="store_true",
        help="no rellenar huecos del tren de referencia con la otra derivación",
    )
    p.add_argument(
        "--no-next-r-guard",
        action="store_true",
        help="no exigir T_off < R siguiente para un QT válido (los métodos de stock: "
        "`production` aplica siempre sus guardas)",
    )
    p.add_argument(
        "--production-thresholds",
        default="",
        help="campos de `IntervalThresholds` para `production`, `campo=valor` coma-separados "
        "(p. ej. `reject_truncated_t=false,max_heart_rate_bpm=200`). `min_beats` sale de "
        "--min-block-beats",
    )
    return p.parse_args(argv)


def parse_overrides(text: str, min_block_beats: int) -> dict[str, Any]:
    """`campo=valor,...` -> kwargs de `IntervalThresholds`; los valores se tipan solos."""
    out: dict[str, Any] = {"min_beats": min_block_beats}
    for item in (x.strip() for x in text.split(",") if x.strip()):
        name, _, raw = item.partition("=")
        value: Any
        if raw.strip().lower() in ("true", "false"):
            value = raw.strip().lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                value = float(raw)
        out[name.strip()] = value
    return out


def _run(jobs: list[Job], workers: int) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_record, j): j.record for j in jobs}
        for k, fut in enumerate(as_completed(futures), 1):
            rec = futures[fut]
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001 — un registro roto no tira la corrida
                meta.append(
                    {
                        "record": rec,
                        "sig_name": [],
                        "n_q1c_beats": 0,
                        "n_usable_beats": 0,
                        "errors": [traceback.format_exc(limit=3)],
                        "misaligned_keys": {},
                        "seconds": {},
                    }
                )
                print(f"[{k}/{len(jobs)}] {rec}: FALLÓ", flush=True)
                continue
            meta.append(res["meta"])
            rows.extend(res["beats"])
            m = res["meta"]
            print(
                f"[{k}/{len(jobs)}] {rec} {m['sig_name']} latidos={m['n_usable_beats']} "
                f"t={m['seconds'].get('total')} s",
                flush=True,
            )
    print(f"delineación: {time.time() - t0:.0f} s", flush=True)
    beats = pd.DataFrame(rows)
    if not beats.empty:
        beats = beats.sort_values(BEAT_ORDER, kind="stable").reset_index(drop=True)
    return beats, sorted(meta, key=lambda m: m["record"])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.download:
        return download()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    if args.variants:
        methods += [m for m in VARIANT_METHODS if m not in methods]
    unknown = [m for m in methods if not known_method(m)]
    if unknown:
        print(f"métodos desconocidos: {unknown}", file=sys.stderr)
        return 2
    production_failed = False
    overrides = parse_overrides(args.production_thresholds, args.min_block_beats)
    if PRODUCTION in methods and not args.summarize_only:
        why = production_status()
        if why is None:
            try:
                production_thresholds(overrides)
            except (TypeError, ValueError) as error:
                print(f"--production-thresholds: {error}", file=sys.stderr)
                return 2
        else:
            methods.remove(PRODUCTION)
            production_failed = True
            notes.append(f"`production` omitido: no se pudo importar `measure_intervals` ({why}).")
            print(f"aviso: {notes[-1]}", file=sys.stderr)
    if PRODUCTION in methods:
        changed = {k: v for k, v in overrides.items() if k != "min_beats" or v != 30}
        if changed:
            notes.append(
                f"`production` corrió con `IntervalThresholds` distintos del default: {changed}."
            )
        if args.no_next_r_guard:
            notes.append(
                "`--no-next-r-guard` afloja solo los métodos de stock: `production` exige "
                "siempre T_off < R siguiente."
            )
    leads = (0, 1) if args.lead == "both" else (int(args.lead),)
    sources = ("reference", "detected") if args.rpeaks == "both" else (args.rpeaks,)

    beats_path, meta_path = out / "beats.csv", out / "records.json"
    if args.summarize_only:
        beats = pd.read_csv(beats_path)
        meta = json.loads(meta_path.read_text())
    else:
        records = [r for r in args.records.split(",") if r] or available_records()
        if not records:
            print("no hay registros: correr con --download", file=sys.stderr)
            return 2
        jobs = [
            Job(
                r,
                leads,
                tuple(methods),
                sources,
                cwt_cache=not args.no_cwt_cache,
                fill_gaps=not args.no_fill_gaps,
                next_r_guard=not args.no_next_r_guard,
                production_overrides=tuple(sorted(overrides.items())),
            )
            for r in records
        ]
        beats, meta = _run(jobs, args.jobs)
        if beats.empty:
            print("ningún latido delineado", file=sys.stderr)
            return 1
        beats.to_csv(beats_path, index=False)
        meta_path.write_text(json.dumps(meta, indent=1, default=str))

    config = report.ReportConfig(
        record_min_beats=args.record_min_beats,
        min_block_beats=args.min_block_beats,
        next_r_guard=not args.no_next_r_guard,
        variants=args.variants,
        n_boot=args.boot,
        fill_gaps=not args.no_fill_gaps,
    )
    verdict = report.write_all(beats, meta, config, out, notes)
    cols = ["label", "lead", "rpeaks", "qrs_cov", "qrs_bias", "qrs_rec_bias", "qt_cov"]
    cols += ["qt_bias", "qt_rec_bias", "qrs_pass", "qt_pass", "qt_ci_pass"]
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(verdict[[c for c in cols if c in verdict]].round(1).to_string(index=False))
    print(f"\n-> {out}/summary.md, verdict.csv, metrics.csv, robustness.csv, beats.csv")
    crashed = [
        f"{m['record']} {e.strip().splitlines()[-1]}"
        for m in meta
        for e in m["errors"]
        if f"/{PRODUCTION}:" in e
    ]
    if production_failed or crashed:
        # El paso en el que la validación vuelve a correr `production` no puede pasar en verde
        # con el módulo afuera o reventando en algún registro.
        for line in crashed[:10]:
            print(f"production falló: {line}", file=sys.stderr)
        return EXIT_PRODUCTION_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
