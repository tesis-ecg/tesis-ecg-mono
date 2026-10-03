"""Métricas e informe del benchmark de QTDB. Ver `qtdb.py` y el README.

Error = estimado − manual (ms). Cobertura = % de latidos anotados con estimación válida.
Gate del plan: |bias| ≤ 20 ms QRS y ≤ 30 ms QT, por latido **y** por mediana de registro.
Además se reportan, como en el benchmark original:

* *bloque*: la mediana de TODOS los latidos del bloque de 300 s con estimación válida
  (mín. 30), lo que guardaría producción, menos la mediana manual del registro;
* IC 95 % por bootstrap de REGISTROS (los latidos de un registro no son independientes):
  "pasa con IC" = los dos IC (por latido y por mediana de registro) enteros dentro del gate;
* acuerdo entre registros (r y pendiente de la mediana estimada contra la manual) y la
  clasificación que harían los hallazgos `qrs_wide` / `qtc_long` con la mediana de bloque.
"""

from __future__ import annotations

import json
import math
import warnings
import zlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tools.physionet.qtdb_delineation import (
    DEFINITIONS,
    PRODUCTION,
    QRS_PLAUSIBLE_MS,
    QT_PLAUSIBLE_MS,
    TARGET_RATE,
    definitions_for,
    method_label,
)

QRS_GATE_MS = 20.0
QT_GATE_MS = 30.0
#: Umbrales de los hallazgos del plan, solo para medir cómo clasificaría la mediana de bloque.
QRS_WIDE_MS = 120.0
QTC_LONG_MS = 470.0
#: QT válido por encima de esto = imposible: un latido perdido por el detector (en
#: `prominence`, sele0203 y sel853 L1) o una T puesta lejos (`cwt`).
QT_MISSED_BEAT_MS = 1000.0
#: `ml_measure_min_beats`: con menos latidos válidos el bloque no reporta mediana.
MIN_BLOCK_BEATS_DEFAULT = 30
MEASURES = ("qrs", "qt", "qtc", "ramp", "ramp_hp")
MEASURE_NAMES = {
    "qrs": "QRS",
    "qt": "QT",
    "qtc": "QTc",
    "ramp": "R_amp_mV",
    "ramp_hp": "R_amp_hp_mV",
}
_BLOCK_COUNT = {"qrs": "n_qrs", "qt": "n_qt", "qtc": "n_qtc", "ramp": "n_qrs", "ramp_hp": "n_qrs"}
#: Orden de las filas en las tablas: los métodos de stock primero, el módulo desplegado al final.
LABEL_ORDER = (
    "dwt",
    "cwt",
    "peak[Q→S; Q→Toff]",
    "prominence",
    "production",
    "prominence~rb200",
    "prominence[Q→S; Q→Toff]",
)


@dataclass(frozen=True)
class ReportConfig:
    record_min_beats: int = 10
    min_block_beats: int = MIN_BLOCK_BEATS_DEFAULT
    next_r_guard: bool = True
    variants: bool = False
    n_boot: int = 2000
    fill_gaps: bool = True


# --------------------------------------------------------------------------- intervalos
def _ms(samples: pd.Series) -> pd.Series:
    return samples.astype(np.float64) * 1000.0 / TARGET_RATE


def compute_intervals(beats: pd.DataFrame, definition: str, next_r_guard: bool) -> pd.DataFrame:
    """Columnas estimado / manual (ms) y máscaras de validez para una definición.

    `valid`: marcas finitas, R_on ≤ R ≤ R_off (QRS) y R_on ≤ R < T_off < R siguiente (QT).
    `plaus`: además dentro de `QRS_PLAUSIBLE_MS` / `QT_PLAUSIBLE_MS`.
    """
    qa, qb = DEFINITIONS[definition]["qrs"]
    ta, tb = DEFINITIONS[definition]["qt"]
    out = beats.copy()
    if "r_amp_hp" not in out:
        out["r_amp_hp"] = np.nan
    out["est_qrs"] = _ms(out[qb] - out[qa])
    out["est_qt"] = _ms(out[tb] - out[ta])
    out["man_qrs"] = _ms(out["man_off"] - out["man_on"])
    out["man_qt"] = _ms(out["man_toff"] - out["man_on"])
    r = out["r_used"]
    ok_qrs = out[qa].notna() & out[qb].notna() & (out[qa] <= r) & (r <= out[qb])
    ok_qt = out[ta].notna() & out[tb].notna() & (out[ta] <= r) & (out[tb] > r)
    out["qt_after_next_r"] = ok_qt & out["r_next"].notna() & (out[tb] >= out["r_next"])
    if next_r_guard:
        ok_qt = ok_qt & ~out["qt_after_next_r"]
    rr = out["rr_prev_s"]
    out["est_qtc"] = out["est_qt"] / np.cbrt(rr)
    out["man_qtc"] = out["man_qt"] / np.cbrt(rr)
    out["valid_qrs"] = ok_qrs
    out["valid_qt"] = ok_qt
    out["valid_qtc"] = ok_qt & rr.notna()
    out["plaus_qrs"] = ok_qrs & out["est_qrs"].between(*QRS_PLAUSIBLE_MS)
    out["plaus_qt"] = ok_qt & out["est_qt"].between(*QT_PLAUSIBLE_MS)
    out["plaus_qtc"] = out["plaus_qt"] & rr.notna()
    # Errores de cada borde: de dónde sale el bias.
    out["err_on"] = _ms(out[qa] - out["man_on"])
    out["err_off"] = _ms(out[qb] - out["man_off"])
    out["err_toff"] = _ms(out[tb] - out["man_toff"])
    # Amplitud R (mV): estimada = sig[R] − sig[R_on] sobre la señal limpia de producción
    # (`ramp`) o sobre el pasaaltos solo (`ramp_hp`); referencia = señal cruda remuestreada
    # en R_ref menos el onset manual. Solo tiene sentido con R_on.
    for name, col in (("ramp", "r_amp"), ("ramp_hp", "r_amp_hp")):
        out[f"est_{name}"] = out[col]
        out[f"man_{name}"] = out["r_amp_ref"]
        out[f"valid_{name}"] = out["valid_qrs"] & out[col].notna()
        out[f"plaus_{name}"] = out["plaus_qrs"] & out[col].notna()
    if "prod_valid" in out and len(out) and (out["method"] == PRODUCTION).all():
        _production_columns(out)
    return out


def _production_columns(out: pd.DataFrame) -> None:
    """`production` no da marcas: QT, QTc y amplitud salen de sus columnas por latido.

    Su `valid` ya incluye las guardas del módulo (orden R_on ≤ R < T_off < R siguiente,
    RR plausible, QT en 200-650 ms), así que vale para `valid` y para `plaus`. El QRS no
    se reporta y la amplitud se mide sobre la señal sin pasabajos (`ramp_hp`).
    """
    valid = out["prod_valid"].astype("boolean").fillna(False).astype(bool)
    rr_ok = out["rr_prev_s"].notna()
    nan = np.full(len(out), np.nan)
    false = np.zeros(len(out), dtype=bool)
    out["est_qt"] = out["prod_qt"].astype(float)
    out["est_qtc"] = out["prod_qtc"].astype(float)
    out["est_ramp_hp"] = out["prod_ramp"].astype(float)
    for col in ("est_qrs", "est_ramp", "err_on", "err_off", "err_toff"):
        out[col] = nan
    for filt in ("valid", "plaus"):
        out[f"{filt}_qt"] = valid
        out[f"{filt}_qtc"] = valid & rr_ok
        out[f"{filt}_ramp_hp"] = valid & out["est_ramp_hp"].notna()
        out[f"{filt}_qrs"] = false
        out[f"{filt}_ramp"] = false
    out["qt_after_next_r"] = false


def interval_frames(beats: pd.DataFrame, config: ReportConfig) -> dict[tuple[str, str], Any]:
    """`compute_intervals` por (método, definición) evaluable."""
    out = {}
    for method in sorted(beats["method"].unique()):
        mb = beats[beats["method"] == method]
        has_plan = bool(mb["r_on"].notna().any() and mb["r_off"].notna().any())
        if method == "production":
            has_plan = True  # puede no dar marcas por latido y solo medianas de bloque
        for definition in definitions_for(method, has_plan, config.variants):
            out[(method, definition)] = compute_intervals(mb, definition, config.next_r_guard)
    return out


# --------------------------------------------------------------------------- métricas
def _stats(err: np.ndarray) -> dict[str, float]:
    err = err[np.isfinite(err)]
    if err.size == 0:
        return {"n": 0, "bias": np.nan, "sd": np.nan, "mae": np.nan, "median": np.nan}
    return {
        "n": int(err.size),
        "bias": float(np.mean(err)),
        "sd": float(np.std(err, ddof=1)) if err.size > 1 else 0.0,
        "mae": float(np.mean(np.abs(err))),
        "median": float(np.median(err)),
    }


def _gate(measure: str) -> float:
    return QRS_GATE_MS if measure == "qrs" else QT_GATE_MS


def summarize_group(
    g: pd.DataFrame, measure: str, filt: str, record_min_beats: int
) -> dict[str, Any]:
    """Por latido y por mediana de registro (registros con ≥ `record_min_beats` válidos)."""
    valid = g[f"{filt}_{measure}"].to_numpy(bool)
    est = g[f"est_{measure}"].to_numpy(float)
    man = g[f"man_{measure}"].to_numpy(float)
    beat = _stats((est - man)[valid])
    rec_err = [
        float(np.median(rg[f"est_{measure}"]) - np.median(rg[f"man_{measure}"]))
        for _, rg in g[valid].groupby("record")
        if len(rg) >= record_min_beats
    ]
    rec = _stats(np.array(rec_err))
    gate = _gate(measure)
    passes = bool(
        beat["n"] > 0 and rec["n"] > 0 and abs(beat["bias"]) <= gate and abs(rec["bias"]) <= gate
    )
    positive = valid & (man > 0)
    return {
        "n_annotated": int(len(g)),
        "n_beats": beat["n"],
        "coverage_pct": 100.0 * beat["n"] / len(g) if len(g) else np.nan,
        "bias_ms": beat["bias"],
        "sd_ms": beat["sd"],
        "mae_ms": beat["mae"],
        "median_err_ms": beat["median"],
        "n_records": rec["n"],
        "n_records_total": int(g["record"].nunique()),
        "record_median_bias_ms": rec["bias"],
        "record_median_sd_ms": rec["sd"],
        "record_median_mae_ms": rec["mae"],
        "gate_ms": gate,
        "passes_gate": passes,
        "man_mean_ms": float(np.nanmean(man[valid])) if valid.any() else np.nan,
        "est_mean_ms": float(np.nanmean(est[valid])) if valid.any() else np.nan,
        "est_max_ms": float(np.nanmax(est[valid])) if valid.any() else np.nan,
        # mediana de estimado / manual con referencia positiva (para la amplitud R)
        "ratio_median": (
            float(np.median(est[positive] / man[positive])) if positive.any() else np.nan
        ),
    }


def _block_lookup(meta: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(m["record"], k): v for m in meta for k, v in m.get("block", {}).items()}


def block_summary(
    g: pd.DataFrame,
    lookup: Mapping[tuple[str, str], dict[str, Any]],
    method: str,
    definition: str,
    filt: str,
    measure: str,
    min_block_beats: int,
) -> dict[str, Any]:
    """Mediana de bloque estilo producción − mediana manual del registro."""
    errs = []
    for (rec, lead, source), rg in g.groupby(["record", "lead", "rpeaks"]):
        st = lookup.get((rec, f"L{lead}/{source}/{method}"), {}).get(f"{definition}/{filt}")
        if not st or st.get(measure) is None or st[_BLOCK_COUNT[measure]] < min_block_beats:
            continue
        man = rg[f"man_{measure}"].to_numpy(float)
        man = man[np.isfinite(man)]
        if man.size:
            errs.append(st[measure] - float(np.median(man)))
    s = _stats(np.array(errs))
    total = int(g["record"].nunique())
    return {
        "block_n_records": s["n"],
        "block_coverage_pct": 100.0 * s["n"] / total if total else np.nan,
        "block_median_bias_ms": s["bias"],
        "block_median_sd_ms": s["sd"],
        "block_median_mae_ms": s["mae"],
    }


def edge_stats(g: pd.DataFrame, filt: str) -> dict[str, Any]:
    out = {}
    for col, measure in (("err_on", "qrs"), ("err_off", "qrs"), ("err_toff", "qt")):
        s = _stats(g[col].to_numpy(float)[g[f"{filt}_{measure}"].to_numpy(bool)])
        out[f"{col}_bias_ms"] = s["bias"]
        out[f"{col}_sd_ms"] = s["sd"]
    return out


def build_metrics(
    beats: pd.DataFrame, meta: list[dict[str, Any]], config: ReportConfig
) -> pd.DataFrame:
    lookup = _block_lookup(meta)
    rows = []
    for (method, definition), iv in interval_frames(beats, config).items():
        for (lead, source), g in iv.groupby(["lead", "rpeaks"]):
            for filt in ("valid", "plaus"):
                for measure in MEASURES:
                    if measure.startswith("ramp") and definition != "plan":
                        continue
                    s = summarize_group(g, measure, filt, config.record_min_beats)
                    b = block_summary(
                        g, lookup, method, definition, filt, measure, config.min_block_beats
                    )
                    if measure in ("qrs", "qt"):
                        bb = b["block_median_bias_ms"]
                        b["passes_block"] = bool(
                            b["block_n_records"] > 0 and abs(bb) <= _gate(measure)
                        )
                    else:
                        s["gate_ms"] = np.nan
                        s["passes_gate"] = None
                    rows.append(
                        {
                            "method": method,
                            "definition": definition,
                            "label": method_label(method, definition),
                            "lead": int(lead),
                            "rpeaks": source,
                            "filter": filt,
                            "measure": MEASURE_NAMES[measure],
                            **s,
                            **b,
                            **(edge_stats(g, filt) if measure in ("qrs", "qt") else {}),
                        }
                    )
    return pd.DataFrame(rows)


def _record_table(g: pd.DataFrame, measure: str, record_min_beats: int) -> pd.DataFrame:
    """Por registro: suma / cuenta del error por latido y error de la mediana (bootstrap)."""
    v = g[g[f"valid_{measure}"]]
    t = pd.DataFrame(
        {
            "record": v["record"],
            "err": v[f"est_{measure}"] - v[f"man_{measure}"],
            "est": v[f"est_{measure}"],
            "man": v[f"man_{measure}"],
        }
    )
    agg = t.groupby("record").agg(
        n=("err", "size"), s=("err", "sum"), est_med=("est", "median"), man_med=("man", "median")
    )
    agg["rec_err"] = np.where(agg["n"] >= record_min_beats, agg["est_med"] - agg["man_med"], np.nan)
    return agg


def _rng(*key: object) -> np.random.Generator:
    """Generador propio de cada configuración: el IC no depende de qué otras se corrieron."""
    return np.random.default_rng(zlib.crc32("|".join(map(str, key)).encode()))


def _classify(estimated: bool, manual: bool) -> str:
    return {(True, True): "tp", (True, False): "fp", (False, True): "fn"}.get(
        (estimated, manual), "tn"
    )


def robustness(
    beats: pd.DataFrame, meta: list[dict[str, Any]], config: ReportConfig
) -> pd.DataFrame:
    """IC 95 % por bootstrap de registros, acuerdo entre registros, techo del QRS y la
    clasificación `qrs_wide` / `qtc_long` con la mediana de bloque contra la manual."""
    lookup = _block_lookup(meta)
    rows = []
    for (method, definition), iv in interval_frames(beats, config).items():
        for (lead, source), g in iv.groupby(["lead", "rpeaks"]):
            label = method_label(method, definition)
            row: dict[str, Any] = {
                "method": method,
                "definition": definition,
                "label": label,
                "lead": int(lead),
                "rpeaks": source,
            }
            for measure in ("qrs", "qt"):
                agg = _record_table(g, measure, config.record_min_beats)
                if agg.empty:
                    continue
                n, sm = agg["n"].to_numpy(float), agg["s"].to_numpy(float)
                rec = agg["rec_err"].to_numpy(float)
                idx = _rng(label, lead, source, measure).integers(
                    0, len(agg), size=(config.n_boot, len(agg))
                )
                beat_b = sm[idx].sum(1) / n[idx].sum(1)
                with warnings.catch_warnings():
                    # remuestreos sin ningún registro con mediana: NaN, sin aviso
                    warnings.simplefilter("ignore", RuntimeWarning)
                    rec_b = np.nanmean(rec[idx], axis=1)
                ci = [float(np.percentile(beat_b, 2.5)), float(np.percentile(beat_b, 97.5))]
                rec_ci = [float(np.nanpercentile(rec_b, q)) for q in (2.5, 97.5)]
                gate = _gate(measure)
                row[f"{measure}_bias_ci"] = ci
                row[f"{measure}_rec_bias_ci"] = rec_ci
                row[f"{measure}_ci_pass"] = bool(
                    -gate <= ci[0] and ci[1] <= gate and -gate <= rec_ci[0] and rec_ci[1] <= gate
                )
                if measure == "qrs":
                    v = g[g["valid_qrs"]]
                    est, man = v["est_qrs"].to_numpy(float), v["man_qrs"].to_numpy(float)
                    wide = man >= QRS_WIDE_MS
                    row["qrs_est_max"] = float(np.max(est)) if est.size else np.nan
                    row["qrs_est_ge98_pct"] = (
                        float(100 * np.mean(est >= 98.0)) if est.size else np.nan
                    )
                    row["qrs_n_wide_beats"] = int(wide.sum())
                    row["qrs_wide_beat_sens_pct"] = (
                        float(100 * np.mean(est[wide] >= QRS_WIDE_MS)) if wide.any() else np.nan
                    )
                ok = agg["n"] >= config.record_min_beats
                if ok.sum() > 2:
                    x = agg.loc[ok, "man_med"].to_numpy(float)
                    y = agg.loc[ok, "est_med"].to_numpy(float)
                    row[f"{measure}_rec_r"] = float(np.corrcoef(x, y)[0, 1])
                    row[f"{measure}_rec_slope"] = float(np.polyfit(x, y, 1)[0])
            row.update(_qtc_agreement(g, config))
            counts = {
                f"{f}_{c}": 0 for f in ("qrs_wide", "qtc_long") for c in ("tp", "fp", "fn", "tn")
            }
            man_rec = g.groupby("record").agg(
                qrs=("man_qrs", "median"), qt=("man_qt", "median"), qtc=("man_qtc", "median")
            )
            pairs: dict[str, list[tuple[float, float]]] = {"qt": [], "qtc": []}
            for rec_name, mr in man_rec.iterrows():
                key = (rec_name, f"L{lead}/{source}/{method}")
                st = lookup.get(key, {}).get(f"{definition}/valid")
                if not st:
                    continue
                for measure, values in pairs.items():
                    if (
                        st[measure] is not None
                        and st[f"n_{measure}"] >= config.min_block_beats
                        and math.isfinite(mr[measure])
                    ):
                        values.append((float(mr[measure]), float(st[measure])))
                if st["qrs"] is not None and st["n_qrs"] >= config.min_block_beats:
                    c = _classify(st["qrs"] >= QRS_WIDE_MS, mr["qrs"] >= QRS_WIDE_MS)
                    counts[f"qrs_wide_{c}"] += 1
                if (
                    st["qtc"] is not None
                    and st["n_qtc"] >= config.min_block_beats
                    and math.isfinite(mr["qtc"])
                ):
                    c = _classify(st["qtc"] >= QTC_LONG_MS, mr["qtc"] >= QTC_LONG_MS)
                    counts[f"qtc_long_{c}"] += 1
            row.update(counts)
            for measure, values in pairs.items():
                if len(values) > 2:
                    x, y = np.array(values).T
                    row[f"{measure}_block_r"] = float(np.corrcoef(x, y)[0, 1])
                    row[f"{measure}_block_slope"] = float(np.polyfit(x, y, 1)[0])
                    if measure == "qtc":
                        long = x >= QTC_LONG_MS
                        row["qtc_long_n"] = int(long.sum())
                        row["qtc_long_block_bias"] = (
                            float(np.median(y[long] - x[long])) if long.any() else np.nan
                        )
                        row["qtc_long_block_ge470"] = int((y[long] >= QTC_LONG_MS).sum())
            rows.append(row)
    return pd.DataFrame(rows)


def _qtc_agreement(g: pd.DataFrame, config: ReportConfig) -> dict[str, float]:
    """Acuerdo del QTc entre registros y de dónde sale el del QT.

    * r y pendiente de la mediana por registro del QTc (el QT arrastra la FC: un r alto del
      QT no dice nada del QTc), con el p90 del error absoluto por registro;
    * r entre el QT **manual** y el RR por registro: la parte del r del QT que es FC;
    * cuántos latidos válidos tienen el T_off topeado en T_peak + 100 ms (`prominence`:
      `peak_prominences(wlen = max_t_basepoint_interval = 200 ms)`), y la distancia manual
      pico de T → fin de T, en general y con QTc manual > 500 ms.
    """
    out: dict[str, float] = {}
    agg = _record_table(g, "qtc", config.record_min_beats)
    ok = agg["n"] >= config.record_min_beats if len(agg) else pd.Series(dtype=bool)
    if ok.sum() > 2:
        x = agg.loc[ok, "man_med"].to_numpy(float)
        y = agg.loc[ok, "est_med"].to_numpy(float)
        out["qtc_rec_r"] = float(np.corrcoef(x, y)[0, 1])
        out["qtc_rec_slope"] = float(np.polyfit(x, y, 1)[0])
        out["qtc_rec_abs_err_p90"] = float(np.percentile(np.abs(y - x), 90))
    v = g[g["valid_qt"]]
    per_rec = v.groupby("record").agg(
        n=("man_qt", "size"), man_qt=("man_qt", "median"), rr=("rr_prev_s", "median")
    )
    per_rec = per_rec[per_rec["n"] >= config.record_min_beats].dropna()
    if len(per_rec) > 2:
        out["qt_man_rr_r"] = float(np.corrcoef(per_rec["man_qt"], per_rec["rr"])[0, 1])
    if "t_peak" in v and v["t_peak"].notna().any():
        tpe = _ms(v["t_off"] - v["t_peak"]).dropna()
        out["t_capped_pct"] = float(100 * np.mean(tpe >= 99.99)) if len(tpe) else np.nan
    if "man_tpeak" in g:
        man_tpe = _ms(g["man_toff"] - g["man_tpeak"])
        out["man_tpe_median_ms"] = float(man_tpe.median())
        out["man_tpe_long_median_ms"] = float(man_tpe[g["man_qtc"] > 500.0].median())
    return out


# --------------------------------------------------------------------------- producción
def _pct(values: pd.Series, q: float) -> float:
    return float(values.quantile(q)) if len(values) else np.nan


def production_blocks(
    beats: pd.DataFrame, meta: list[dict[str, Any]], config: ReportConfig
) -> pd.DataFrame:
    """Lo que guardaría producción por bloque de 300 s, por derivación × fuente de R.

    * cuántos bloques salen con medición (`measure_intervals` no devuelve None) sobre los
      que tienen latidos anotados, y el `coverage_ratio` (válidos / candidatos) de esos;
    * el QT, el QTc y la amplitud R de bloque contra la mediana manual del registro;
    * la amplitud R por latido contra la referencia (señal cruda en R_ref − onset manual).
      La de producción sale de `raw_for_amplitude` (ver `production_amplitude_signal`); la
      de `prominence` sobre la señal limpia se pone al lado para ver el pasabajos.
    """
    prod = beats[beats["method"] == PRODUCTION]
    if prod.empty:
        return pd.DataFrame()
    lookup = _block_lookup(meta)
    stock = beats[beats["method"] == "prominence"]
    rows = []
    for (lead, source), g in prod.groupby(["lead", "rpeaks"]):
        blocks = []
        for rec, rg in g.groupby("record"):
            st = lookup.get((rec, f"L{lead}/{source}/{PRODUCTION}"), {}).get("plan/valid", {})
            reported = st.get("qt") is not None and st.get("n_qt", 0) >= config.min_block_beats
            man_amp = rg["r_amp_ref"].to_numpy(float)
            blocks.append(
                {
                    "reported": reported,
                    "coverage_ratio": st.get("coverage_ratio") if reported else np.nan,
                    "hr": st.get("heart_rate_bpm") if reported else np.nan,
                    "amp": st.get("ramp_hp") if reported else np.nan,
                    "man_amp": float(np.median(man_amp)) if man_amp.size else np.nan,
                }
            )
        b = pd.DataFrame(blocks)
        ok = b[b["reported"]]
        cov = ok["coverage_ratio"].astype(float)
        positive = ok[ok["man_amp"] > 0]
        valid = g["prod_valid"].astype("boolean").fillna(False).astype(bool)
        est, ref = g["prod_ramp"].to_numpy(float), g["r_amp_ref"].to_numpy(float)
        pos = valid.to_numpy() & (ref > 0) & np.isfinite(est)
        row: dict[str, Any] = {
            "lead": int(lead),
            "rpeaks": source,
            "blocks": len(b),
            "blocks_reported": int(b["reported"].sum()),
            "block_coverage_pct": 100.0 * float(b["reported"].mean()) if len(b) else np.nan,
            "coverage_ratio_mean": float(cov.mean()) if len(cov) else np.nan,
            "coverage_ratio_median": float(cov.median()) if len(cov) else np.nan,
            "coverage_ratio_p10": _pct(cov, 0.10),
            "hr_median_bpm": float(ok["hr"].astype(float).median()) if len(ok) else np.nan,
            "amp_beat_ratio_median": float(np.median(est[pos] / ref[pos])) if pos.any() else np.nan,
            "amp_beat_bias_mv": float(np.mean((est - ref)[valid])) if valid.any() else np.nan,
            "amp_block_ratio_median": (
                float(np.median(positive["amp"].astype(float) / positive["man_amp"]))
                if len(positive)
                else np.nan
            ),
            "amp_block_bias_mv": (
                float(np.mean(ok["amp"].astype(float) - ok["man_amp"])) if len(ok) else np.nan
            ),
        }
        sg = stock[(stock["lead"] == lead) & (stock["rpeaks"] == source)]
        clean = sg["r_amp"].to_numpy(float)
        sref = sg["r_amp_ref"].to_numpy(float)
        spos = np.isfinite(clean) & (sref > 0)
        row["prominence_clean_ratio_median"] = (
            float(np.median(clean[spos] / sref[spos])) if spos.any() else np.nan
        )
        rows.append(row)
    return pd.DataFrame(rows)


#: Guardas propias del módulo (columnas `prod_<control>` de `measure_production`), en el
#: orden en que se atribuye un latido perdido: la primera que falla se lleva el latido.
PRODUCTION_GUARDS = ("candidate", "rr_ok", "peak_ok", "t_contained", "qrs_positive", "dominant_ok")


def production_parity(
    beats: pd.DataFrame, meta: list[dict[str, Any]], config: ReportConfig
) -> pd.DataFrame:
    """`production` contra `prominence`: es el mismo delineador.

    La referencia es `prominence` con el filtro `valid` y el rango de QT del módulo
    (`qt_min_ms`-`qt_max_ms`; con los defaults, 200-650 ms, el mismo del filtro `plaus`).
    Por latido el QT tiene que ser idéntico donde los dos lo dan por válido, y todo latido
    que la referencia acepta y producción no tiene que explicarse por alguna guarda propia
    del módulo, leída de las máscaras que él mismo expone (`BeatIntervals`): sin R previo,
    RR fuera de sus límites, R fuera del pico, T cortada, QRS negativo. `unexplained` > 0 o
    `prod_only` > 0 es una discrepancia entre el módulo y el harness. Por bloque se compara
    con la mediana de `prominence`/`plaus`.
    """
    key = ["record", "lead", "rpeaks", "beat"]
    stock = beats[beats["method"] == "prominence"].set_index(key)
    prod = beats[beats["method"] == PRODUCTION].set_index(key)
    common = stock.index.intersection(prod.index)
    if common.empty:
        return pd.DataFrame()
    s, p = stock.loc[common], prod.loc[common]
    lookup = _block_lookup(meta)
    iv = compute_intervals(s.reset_index(), "plan", next_r_guard=True).set_index(key)

    def mask(name: str) -> pd.Series:
        column = f"prod_{name}"
        if column not in p:
            return pd.Series(True, index=common)
        return p[column].astype("boolean").fillna(False).astype(bool)

    pv = mask("valid")
    names = ("qt_min_ms", "qt_max_ms")
    limits: dict[str, list[float]] = {name: [] for name in names}
    for rec, lead, source, _ in common:
        st = lookup.get((rec, f"L{lead}/{source}/{PRODUCTION}"), {}).get("plan/valid", {})
        for name, values in limits.items():
            values.append(st.get(name) or np.nan)
    qt_lo, qt_hi = (pd.Series(limits[k], index=common, dtype=float) for k in names)
    # Sin `qt_*_ms` en el bloque (el módulo no llegó a delinear) vale el rango de `plaus`.
    qt_lo, qt_hi = qt_lo.fillna(QT_PLAUSIBLE_MS[0]), qt_hi.fillna(QT_PLAUSIBLE_MS[1])
    plaus = iv["valid_qt"].astype(bool) & (iv["est_qt"] >= qt_lo) & (iv["est_qt"] <= qt_hi)
    lost = plaus & ~pv
    remaining = lost.copy()
    frame = pd.DataFrame(
        {
            "both": plaus & pv,
            "lost": lost,
            "prod_only": pv & ~plaus,
            "diff": (p["prod_qt"].astype(float) - iv["est_qt"]).abs().where(plaus & pv),
        }
    )
    for guard in PRODUCTION_GUARDS:
        by_guard = remaining & ~mask(guard)
        frame[f"by_{guard}"] = by_guard
        remaining = remaining & ~by_guard
    frame["unexplained"] = remaining
    rows = []
    for (lead, source), g in frame.groupby(level=["lead", "rpeaks"]):
        row: dict[str, Any] = {
            "lead": int(lead),
            "rpeaks": source,
            "both_valid": int(g["both"].sum()),
            "qt_max_abs_diff_ms": float(g["diff"].max()) if g["both"].any() else np.nan,
            "lost_vs_plaus": int(g["lost"].sum()),
        }
        for guard in PRODUCTION_GUARDS:
            row[f"lost_by_{guard}"] = int(g[f"by_{guard}"].sum())
        row["unexplained"] = int(g["unexplained"].sum())
        row["prod_only"] = int(g["prod_only"].sum())
        rows.append(row)
    out = pd.DataFrame(rows)
    blocks = []
    for (rec, key_), st in lookup.items():
        lead_s, source, method = key_.split("/")
        if method != PRODUCTION:
            continue
        ref = lookup.get((rec, f"{lead_s}/{source}/prominence"), {}).get("plan/plaus")
        if not ref:
            continue
        q, r = st["plan/valid"], ref
        q_ok = q.get("qt") is not None
        r_ok = r.get("qt") is not None and r["n_qt"] >= config.min_block_beats
        blocks.append(
            {
                "lead": int(lead_s[1:]),
                "rpeaks": source,
                "both": q_ok and r_ok,
                "same": q_ok and r_ok and abs(q["qt"] - r["qt"]) < 1e-9,
                "delta": abs(q["qt"] - r["qt"]) if q_ok and r_ok else np.nan,
                "stock_only": r_ok and not q_ok,
            }
        )
    if blocks:
        bdf = (
            pd.DataFrame(blocks)
            .groupby(["lead", "rpeaks"])
            .agg(
                blocks_both=("both", "sum"),
                blocks_same_median=("same", "sum"),
                block_max_abs_diff_ms=("delta", "max"),
                blocks_stock_only=("stock_only", "sum"),
            )
        )
        out = out.merge(bdf.reset_index(), on=["lead", "rpeaks"], how="left")
    return out


# --------------------------------------------------------------------------- veredicto
def _label_key(label: str) -> tuple[int, str]:
    return (LABEL_ORDER.index(label) if label in LABEL_ORDER else len(LABEL_ORDER), label)


def verdict_table(
    metrics: pd.DataFrame, rob: pd.DataFrame | None = None, filt: str = "valid"
) -> pd.DataFrame:
    """Una fila por (configuración, derivación, fuente de R): QRS y QT lado a lado."""
    m = metrics[(metrics["filter"] == filt) & metrics["measure"].isin(["QRS", "QT"])]
    ci = {}
    if rob is not None and len(rob):
        for _, r in rob.iterrows():
            ci[(r["label"], int(r["lead"]), r["rpeaks"])] = (
                r.get("qrs_ci_pass"),
                r.get("qt_ci_pass"),
            )
    rows = []
    for (label, definition, lead, source), g in m.groupby(
        ["label", "definition", "lead", "rpeaks"]
    ):
        q = g[g["measure"] == "QRS"].iloc[0]
        t = g[g["measure"] == "QT"].iloc[0]
        row = {"label": label, "definition": definition, "lead": int(lead), "rpeaks": source}
        for prefix, r in (("qrs", q), ("qt", t)):
            row.update(
                {
                    f"{prefix}_cov": r["coverage_pct"],
                    f"{prefix}_bias": r["bias_ms"],
                    f"{prefix}_sd": r["sd_ms"],
                    f"{prefix}_mae": r["mae_ms"],
                    f"{prefix}_rec_bias": r["record_median_bias_ms"],
                    f"{prefix}_rec_mae": r["record_median_mae_ms"],
                    f"{prefix}_block_bias": r["block_median_bias_ms"],
                    # sin latidos válidos = no se reporta (el QRS de `production`)
                    f"{prefix}_pass": None if r["n_beats"] == 0 else bool(r["passes_gate"]),
                }
            )
        cq, ct = ci.get((label, int(lead), source), (None, None))
        row["qrs_ci_pass"] = _bool_or_none(cq)
        row["qt_ci_pass"] = _bool_or_none(ct)
        rows.append(row)
    v = pd.DataFrame(rows)
    if len(v):
        v["both_pass"] = v["qrs_pass"].eq(True) & v["qt_pass"].eq(True)
        v["_k"] = v["label"].map(_label_key)
        v = v.sort_values(["lead", "rpeaks", "_k"], ascending=[True, False, True]).drop(
            columns="_k"
        )
    return v.reset_index(drop=True)


def _bool_or_none(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return bool(value)


def gate_overview(verdict: pd.DataFrame) -> pd.DataFrame:
    """Por configuración: en cuántas combinaciones derivación × fuente de R pasa cada gate."""
    rows = []
    for label, g in verdict.groupby("label"):
        rows.append(
            {
                "label": label,
                "combos": len(g),
                "qrs_reported": bool(g["qrs_pass"].notna().any()),
                "qrs_pass": int(g["qrs_pass"].eq(True).sum()),
                "qt_pass": int(g["qt_pass"].eq(True).sum()),
                "qrs_ci_pass": int(g["qrs_ci_pass"].eq(True).sum()),
                "qt_ci_pass": int(g["qt_ci_pass"].eq(True).sum()),
                "both_pass": int(g["both_pass"].sum()),
                "qt_bias_range": (float(g["qt_bias"].min()), float(g["qt_bias"].max())),
                "qrs_bias_range": (float(g["qrs_bias"].min()), float(g["qrs_bias"].max())),
            }
        )
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("label", key=lambda s: s.map(_label_key)).reset_index(drop=True)
    return out


# --------------------------------------------------------------------------- informe
def _fmt(v: Any, d: int = 1) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:.{d}f}"


def _ci(v: Any) -> str:
    return "—" if not isinstance(v, list | tuple) else f"[{v[0]:.1f}, {v[1]:.1f}]"


def _int(v: Any) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else str(int(v))


def _range(v: tuple[float, float]) -> str:
    return "—" if not math.isfinite(v[0]) else f"{_fmt(v[0])} a {_fmt(v[1])}"


def _pass(v: Any) -> str:
    return "—" if v is None else ("PASA" if v else "no")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(r) + " |" for r in rows]


def _notes(beats: pd.DataFrame, meta: list[dict[str, Any]], config: ReportConfig) -> list[str]:
    notes = []
    errors = [f"{m['record']} {e}" for m in meta for e in m["errors"]]
    notes.append(
        f"Corridas de delineación sin resultado (guarda de crash): {len(errors)}"
        + "".join(f"\n  - `{e.strip().splitlines()[-1][:160]}`" for e in errors[:20])
    )
    mis = [(m["record"], k) for m in meta for k, v in m["misaligned_keys"].items() if v > 0]
    notes.append(
        f"Corridas en las que la API pública `nk.ecg_delineate` habría desalineado arrays "
        f"(descarta los valores ≤ 0 en vez de volverlos NaN): {len(mis)}"
        + (f" ({', '.join(sorted({k.split('/')[-1] for _, k in mis}))})" if mis else "")
        + ". El harness llama al despachador interno y los convierte en NaN."
    )
    gaps = [(m["record"], k, g) for m in meta for k, g in m.get("gaps", {}).items()]
    if gaps:
        resid = [(r, k, g["residual"]) for r, k, g in gaps if g["residual"]]
        notes.append(
            f"Huecos del tren de referencia: {sum(1 for *_, g in gaps if g['added'])} trenes "
            f"rellenados con {sum(g['added'] for *_, g in gaps)} R de la otra derivación; "
            f"trenes con huecos residuales (pausas, FA): {len(resid)}."
        )
    for (method, definition), iv in interval_frames(beats, config).items():
        if definition != "plan" and method != "peak":
            continue
        label = method_label(method, definition)
        after = iv.groupby(["lead", "rpeaks"])["qt_after_next_r"].sum()
        if after.sum():
            notes.append(
                f"Latidos con T_off ≥ R siguiente ({label}, excluidos del QT): "
                + ", ".join(f"L{lead}/{src} {int(n)}" for (lead, src), n in after.items())
            )
        long_qt = iv[iv["valid_qt"] & (iv["est_qt"] > QT_MISSED_BEAT_MS)]
        if len(long_qt):
            where = sorted({f"{r} L{lead}" for r, lead in zip(long_qt["record"], long_qt["lead"])})
            notes.append(
                f"Latidos con QT válido > {QT_MISSED_BEAT_MS:.0f} ms ({label}; imposibles: un "
                "latido perdido por el detector o una T mal ubicada, solo los saca el filtro "
                f"`plaus`): {len(long_qt)} en " + ", ".join(where[:8])
            )
    matched = {
        f"L{k}": sum(m.get("n_detected_matched", {}).get(f"L{k}", 0) for m in meta) for k in (0, 1)
    }
    notes.append(f"R detectados pareados (≤ 75 ms) con latidos anotados: {matched}.")
    if "snap_fallback" in beats:
        one = beats.drop_duplicates(["record", "lead", "beat"])
        fb = one.groupby("lead")["snap_fallback"].agg(["sum", "size"])
        notes.append(
            "R de referencia sin máximo local en el QRS manual ± 20 ms (se usa argmax): "
            + ", ".join(f"L{lead} {int(r['sum'])}/{int(r['size'])}" for lead, r in fb.iterrows())
        )
    return notes


def _production_lines(
    metrics: pd.DataFrame, prod_blocks: pd.DataFrame | None, parity: pd.DataFrame | None
) -> list[str]:
    """Secciones de `production`: bloques que reporta, cobertura, amplitud R y paridad."""
    f = _fmt
    lines: list[str] = []
    if prod_blocks is not None and len(prod_blocks):
        block = metrics[
            (metrics["method"] == PRODUCTION)
            & (metrics["filter"] == "valid")
            & metrics["measure"].isin(["QT", "QTc"])
        ]

        def block_bias(lead: int, source: str, measure: str) -> str:
            m = block[
                (block["lead"] == lead)
                & (block["rpeaks"] == source)
                & (block["measure"] == measure)
            ]
            if m.empty:
                return "—"
            r = m.iloc[0]
            return f"{f(r['block_median_bias_ms'])} ({f(r['block_median_mae_ms'])})"

        lines += [
            "",
            "## `production` (`app.ml.intervals`): bloques de 300 s, cobertura y amplitud R",
            "",
            "*bloques* = bloques con medición (`measure_intervals` no devuelve None) sobre los que "
            "tienen latidos anotados. *coverage_ratio* = válidos / candidatos del bloque. QT y QTc "
            "de bloque: bias (MAE) de la mediana de bloque contra la mediana manual, en ms. "
            "Amplitud R contra la cruda en R_ref − onset manual: mediana de estimado/referencia "
            "por latido y por bloque, bias en mV; *limpia* es `prominence` sobre la señal limpia.",
            "",
        ]
        rows = [
            [str(r["lead"]), r["rpeaks"]]
            + [
                f"{r['blocks_reported']}/{r['blocks']} ({f(r['block_coverage_pct'])} %)",
                f"{f(r['coverage_ratio_mean'], 3)} / {f(r['coverage_ratio_median'], 3)} / "
                f"{f(r['coverage_ratio_p10'], 3)}",
                block_bias(r["lead"], r["rpeaks"], "QT"),
                block_bias(r["lead"], r["rpeaks"], "QTc"),
                f(r["hr_median_bpm"], 0),
                f(r["amp_beat_ratio_median"], 3),
                f(r["amp_block_ratio_median"], 3),
                f(r["amp_beat_bias_mv"], 3),
                f(r["prominence_clean_ratio_median"], 3),
            ]
            for _, r in prod_blocks.sort_values(
                ["lead", "rpeaks"], ascending=[True, False]
            ).iterrows()
        ]
        lines += _table(
            [
                "deriv.",
                "R",
                "bloques",
                "coverage_ratio media / mediana / p10",
                "QT bloque",
                "QTc bloque",
                "FC mediana",
                "amp. est/ref latido",
                "amp. est/ref bloque",
                "amp. bias mV",
                "amp. limpia est/ref",
            ],
            rows,
        )
    if parity is not None and len(parity):
        lines += [
            "",
            "## `production` vs. `prominence` con filtro `plaus` (mismo delineador)",
            "",
            "Donde los dos aceptan el latido el QT tiene que ser idéntico; los latidos que "
            "`prominence`/`plaus` acepta y `production` no tienen que explicarse por alguna "
            "guarda propia del módulo, leída de sus máscaras (`BeatIntervals`) y atribuida a la "
            "primera que falla: sin R previo, RR fuera de [piso, techo] (rango absoluto, "
            "prematuridad, techo de FC, hueco del detector), R fuera del pico del QRS, T "
            "cortada, QRS negativo. *sin explicar* o *solo production* > 0 es una discrepancia.",
            "",
        ]
        guard_cols = {
            "candidate": "sin R previo",
            "rr_ok": "RR",
            "peak_ok": "R fuera del pico",
            "t_contained": "T cortada",
            "qrs_positive": "QRS negativo",
            "dominant_ok": "no dominante",
        }
        rows = [
            [str(r["lead"]), r["rpeaks"], str(r["both_valid"]), f(r["qt_max_abs_diff_ms"], 2)]
            + [str(r["lost_vs_plaus"])]
            + [str(int(r.get(f"lost_by_{g}", 0))) for g in guard_cols]
            + [str(r["unexplained"]), str(r["prod_only"])]
            + [
                f"{int(r.get('blocks_same_median', 0))}/{int(r.get('blocks_both', 0))}",
                f(r.get("block_max_abs_diff_ms")),
                str(int(r.get("blocks_stock_only", 0))),
            ]
            for _, r in parity.sort_values(["lead", "rpeaks"], ascending=[True, False]).iterrows()
        ]
        lines += _table(
            [
                "deriv.",
                "R",
                "latidos válidos en los dos",
                "ΔQT máx. (abs., ms)",
                "solo `plaus`",
                *guard_cols.values(),
                "sin explicar",
                "solo production",
                "bloques con la misma mediana",
                "Δ bloque máx. (abs., ms)",
                "bloques solo `prominence`",
            ],
            rows,
        )
    return lines


def write_summary(
    path: Path,
    metrics: pd.DataFrame,
    verdict: pd.DataFrame,
    rob: pd.DataFrame,
    beats: pd.DataFrame,
    meta: list[dict[str, Any]],
    config: ReportConfig,
    extra_notes: list[str],
    mlii: pd.DataFrame | None,
    *,
    prod_blocks: pd.DataFrame | None = None,
    parity: pd.DataFrame | None = None,
) -> None:
    f = _fmt
    n_rec = sum(1 for m in meta if m["n_usable_beats"] > 0)
    n_beats = sum(m["n_usable_beats"] for m in meta)
    one = beats.drop_duplicates(["record", "beat"])
    man_qrs = (one["man_off"] - one["man_on"]) * 1000.0 / TARGET_RATE
    man_qt = (one["man_toff"] - one["man_on"]) * 1000.0 / TARGET_RATE
    lines = [
        "# QTDB — delineación NeuroKit 0.2.13 vs. anotaciones manuales (q1c)",
        "",
        f"- {n_rec} registros, {n_beats} latidos anotados utilizables (manual: QRS "
        f"{man_qrs.mean():.0f} ± {man_qrs.std():.0f} ms, QT {man_qt.mean():.0f} ± "
        f"{man_qt.std():.0f} ms).",
        "- Réplica de producción: 250→500 Hz, `clean_signal` / `detect_rpeaks` de "
        "`app.ml.rpeak_detection`, bloques de 300 s centrados en el tramo anotado.",
        f"- Gate: |bias| ≤ {QRS_GATE_MS:.0f} ms QRS y ≤ {QT_GATE_MS:.0f} ms QT, por latido **y** "
        f"por mediana de registro (≥ {config.record_min_beats} latidos válidos). *bloque* = "
        f"mediana de todos los latidos del bloque con ≥ {config.min_block_beats} válidos − "
        f"mediana manual. *IC* = IC95 por bootstrap de registros ({config.n_boot} remuestreos), "
        "por latido y por registro, enteros dentro del gate.",
        "- Definición del plan: QRS = R_on→R_off, QT = R_on→T_off. QT válido: R_on ≤ R < T_off"
        + (" < R siguiente del tren." if config.next_r_guard else " (sin guarda de R siguiente)."),
        f"- Tren de referencia con relleno de huecos: {'sí' if config.fill_gaps else 'no'}.",
        "- Versiones: " + ", ".join(f"{k} {v}" for k, v in package_versions().items()) + ".",
        "",
        "## Gate por configuración (combinaciones derivación × fuente de R que pasan)",
        "",
    ]
    rows = []
    for _, r in gate_overview(verdict).iterrows():
        rows.append(
            [
                r["label"],
                f"{r['qrs_pass']}/{r['combos']}" if r["qrs_reported"] else "no se reporta",
                f"{r['qrs_ci_pass']}/{r['combos']}" if r["qrs_reported"] else "—",
                f"{r['qt_pass']}/{r['combos']}",
                f"{r['qt_ci_pass']}/{r['combos']}",
                f"{r['both_pass']}/{r['combos']}" if r["qrs_reported"] else "—",
                _range(r["qrs_bias_range"]),
                _range(r["qt_bias_range"]),
            ]
        )
    lines += _table(
        ["método", "QRS", "QRS con IC", "QT", "QT con IC", "ambos", "QRS bias", "QT bias"], rows
    )
    for lead in sorted(verdict["lead"].unique()):
        lines += ["", f"## Derivación {lead} — filtro `valid`", ""]
        rows = []
        for _, r in verdict[verdict["lead"] == lead].iterrows():
            rows.append(
                [r["label"], r["rpeaks"]]
                + [
                    f(r[f"{p}_{c}"])
                    for p in ("qrs", "qt")
                    for c in ("cov", "bias", "sd", "mae", "rec_bias", "rec_mae", "block_bias")
                ]
                + [_pass(r["qrs_pass"]), _pass(r["qt_pass"])]
                + [_pass(r["qrs_ci_pass"]), _pass(r["qt_ci_pass"])]
            )
        cols = ["cob. %", "bias", "SD", "MAE", "bias reg.", "MAE reg.", "bloque"]
        lines += _table(
            ["método", "R"]
            + [f"{p} {c}" for p in ("QRS", "QT") for c in cols]
            + ["QRS", "QT", "QRS IC", "QT IC"],
            rows,
        )
    qtc = metrics[(metrics["filter"] == "valid") & (metrics["measure"] == "QTc")]
    if len(qtc):
        lines += ["", "## QTc Fridericia (informativo, sin gate en el plan)", ""]
        rows = [
            [r["label"], str(r["lead"]), r["rpeaks"], str(r["n_beats"])]
            + [f(r[c]) for c in ("coverage_pct", "bias_ms", "sd_ms", "mae_ms")]
            + [f(r["record_median_bias_ms"]), f(r["block_median_bias_ms"])]
            for _, r in _sorted(qtc).iterrows()
        ]
        lines += _table(
            ["método", "deriv.", "R", "n", "cob. %", "bias", "SD", "MAE", "bias reg.", "bloque"],
            rows,
        )
    lines += ["", "## Errores por borde (bias ± SD, ms; filtro `valid`)", ""]
    edge = metrics[(metrics["filter"] == "valid") & (metrics["measure"] == "QRS")]
    qt_edge = metrics[(metrics["filter"] == "valid") & (metrics["measure"] == "QT")]
    rows = []
    for _, r in _sorted(edge).iterrows():
        t = qt_edge[
            (qt_edge["label"] == r["label"])
            & (qt_edge["lead"] == r["lead"])
            & (qt_edge["rpeaks"] == r["rpeaks"])
        ]
        toff = (
            f"{f(t['err_toff_bias_ms'].iloc[0])} ± {f(t['err_toff_sd_ms'].iloc[0])}"
            if len(t)
            else "—"
        )
        rows.append(
            [
                r["label"],
                str(r["lead"]),
                r["rpeaks"],
                f"{f(r['err_on_bias_ms'])} ± {f(r['err_on_sd_ms'])}",
                f"{f(r['err_off_bias_ms'])} ± {f(r['err_off_sd_ms'])}",
                toff,
            ]
        )
    lines += _table(["método", "deriv.", "R", "QRS onset", "QRS offset", "T offset"], rows)
    plaus = verdict_table(metrics, None, "plaus")
    if len(plaus):
        lines += [
            "",
            f"## Filtro `plaus` (QRS {QRS_PLAUSIBLE_MS}, QT {QT_PLAUSIBLE_MS} ms): la guarda de "
            "plausibilidad que necesita producción",
            "",
        ]
        rows = [
            [r["label"], str(r["lead"]), r["rpeaks"]]
            + [f(r[c]) for c in ("qrs_cov", "qrs_bias", "qrs_rec_bias", "qt_cov", "qt_bias")]
            + [f(r["qt_rec_bias"]), f(r["qt_block_bias"]), _pass(r["qt_pass"])]
            for _, r in plaus.iterrows()
        ]
        lines += _table(
            [
                "método",
                "deriv.",
                "R",
                "QRS cob. %",
                "QRS bias",
                "QRS bias reg.",
                "QT cob. %",
                "QT bias",
                "QT bias reg.",
                "QT bloque",
                "QT",
            ],
            rows,
        )
    ramp = metrics[
        (metrics["filter"] == "valid")
        & metrics["measure"].str.startswith("R_amp")
        & (metrics["n_beats"] > 0)
    ]
    if len(ramp):
        lines += [
            "",
            "## Amplitud R (mV): sig[R] − sig[R_on] vs. señal cruda en R_ref − onset manual",
            "",
            "`R_amp_mV` sobre la señal limpia de producción (`nk.ecg_clean`, con el pasabajos de "
            "~16 Hz de `powerline`); `R_amp_hp_mV` sobre el pasaaltos de 0,5 Hz solo. "
            "`production` mide sobre `raw_for_amplitude` (pasaaltos de 0,5 Hz + notch de red "
            "de `quality.remove_mains`), con sus R_onset y su filtro `valid`.",
            "",
        ]
        rows = [
            [
                r["label"],
                "raw_for_amplitude" if r["method"] == PRODUCTION else r["measure"],
                str(r["lead"]),
                r["rpeaks"],
                str(r["n_beats"]),
            ]
            + [f(r[c], 3) for c in ("bias_ms", "sd_ms", "man_mean_ms", "est_mean_ms")]
            + [f(r["ratio_median"], 2)]
            for _, r in _sorted(ramp).iterrows()
        ]
        lines += _table(
            [
                "método",
                "señal",
                "deriv.",
                "R",
                "n",
                "bias",
                "SD",
                "media ref.",
                "media est.",
                "mediana est/ref",
            ],
            rows,
        )
    lines += _production_lines(metrics, prod_blocks, parity)
    if len(rob):
        lines += [
            "",
            "## Robustez: IC, acuerdo entre registros y hallazgos con mediana de bloque",
            "",
            f"`qrs_wide` (≥ {QRS_WIDE_MS:.0f} ms) y `qtc_long` (≥ {QTC_LONG_MS:.0f} ms) con la "
            "mediana de bloque contra la mediana manual del registro: VP/FP/FN/VN.",
            "",
        ]
        rows = []
        for _, r in _sorted(rob).iterrows():
            rows.append(
                [r["label"], str(r["lead"]), r["rpeaks"]]
                + [_ci(r.get("qrs_bias_ci")), _ci(r.get("qrs_rec_bias_ci"))]
                + [f(r.get("qrs_rec_r"), 2), f(r.get("qrs_rec_slope"), 2)]
                + [_ci(r.get("qt_bias_ci")), _ci(r.get("qt_rec_bias_ci"))]
                + [f(r.get("qt_rec_r"), 2), f(r.get("qt_rec_slope"), 2)]
                + ["/".join(str(r[f"qrs_wide_{c}"]) for c in ("tp", "fp", "fn", "tn"))]
                + ["/".join(str(r[f"qtc_long_{c}"]) for c in ("tp", "fp", "fn", "tn"))]
            )
        lines += _table(
            [
                "método",
                "deriv.",
                "R",
                "QRS IC",
                "QRS IC reg.",
                "QRS r",
                "QRS pend.",
                "QT IC",
                "QT IC reg.",
                "QT r",
                "QT pend.",
                "qrs_wide",
                "qtc_long",
            ],
            rows,
        )
        lines += [
            "",
            "## Acuerdo del QTc entre registros",
            "",
            "El r del QT arrastra la FC (*r QT man.–RR*: el QT manual contra el RR, por "
            "registro); el del QTc es el que dice si la medición sigue al paciente. *reg.* = "
            "mediana por registro de los latidos anotados; *bloque* = mediana de bloque contra la "
            "mediana manual; *|err| p90* = p90 del error absoluto del QTc por registro. *QTc ≥ "
            f"{QTC_LONG_MS:.0f}*: registros con QTc manual ≥ {QTC_LONG_MS:.0f} ms, mediana del "
            "error de bloque y cuántos salen ≥ el umbral. *T_off topeado*: latidos válidos con "
            "T_off = T_peak + 100 ms (techo de `peak_prominences(wlen = 200 ms)`). *Tpe man.*: "
            "pico → fin de T manual, mediana general / con QTc manual > 500 ms.",
            "",
        ]
        rows = []
        for _, r in _sorted(rob).iterrows():
            rows.append(
                [r["label"], str(r["lead"]), r["rpeaks"]]
                + [f(r.get("qt_rec_r"), 2), f(r.get("qt_man_rr_r"), 2)]
                + [f(r.get("qtc_rec_r"), 2), f(r.get("qtc_rec_slope"), 2)]
                + [f(r.get("qtc_rec_abs_err_p90"), 0)]
                + [f(r.get("qtc_block_r"), 2), f(r.get("qtc_block_slope"), 2)]
                + [
                    f"{f(r.get('qtc_long_block_bias'), 0)} "
                    f"({_int(r.get('qtc_long_block_ge470'))}/{_int(r.get('qtc_long_n'))})",
                    f(r.get("t_capped_pct"), 0),
                    f"{f(r.get('man_tpe_median_ms'), 0)} / {f(r.get('man_tpe_long_median_ms'), 0)}",
                ]
            )
        lines += _table(
            [
                "método",
                "deriv.",
                "R",
                "QT r reg.",
                "r QT man.–RR",
                "QTc r reg.",
                "QTc pend. reg.",
                "QTc |err| p90",
                "QTc r bloque",
                "QTc pend. bloque",
                f"QTc ≥ {QTC_LONG_MS:.0f}: bias bloque (≥ umbral / n)",
                "T_off topeado %",
                "Tpe man. ms",
            ],
            rows,
        )
        lines += [
            "",
            "## Techo del QRS y sensibilidad a QRS ancho por latido",
            "",
            "`prominence` usa `peak_prominences(wlen = max_r_basepoint_interval = 100 ms)`: R_on y "
            "R_off quedan a ≤ 50 ms del R, así que R_on→R_off nunca supera 100 ms.",
            "",
        ]
        rows = [
            [r["label"], str(r["lead"]), r["rpeaks"], f(r.get("qrs_est_max"))]
            + [f(r.get("qrs_est_ge98_pct")), str(r.get("qrs_n_wide_beats", "—"))]
            + [f(r.get("qrs_wide_beat_sens_pct"))]
            for _, r in _sorted(rob).iterrows()
        ]
        lines += _table(
            [
                "método",
                "deriv.",
                "R",
                "QRS est. máx.",
                "% est. ≥ 98 ms",
                "latidos manual ≥ 120",
                "% de ellos est. ≥ 120",
            ],
            rows,
        )
    if mlii is not None and len(mlii):
        lines += [
            "",
            "## Subconjunto MLII (la derivación más parecida al chaleco; informativo)",
            "",
        ]
        rows = [
            [r["label"], str(r["lead"]), r["rpeaks"], str(r["n_rec"])]
            + [f(r[c]) for c in ("qrs_bias", "qrs_rec_bias", "qt_bias", "qt_rec_bias")]
            + [_pass(r["qrs_pass"]), _pass(r["qt_pass"])]
            for _, r in mlii.iterrows()
        ]
        lines += _table(
            ["método", "deriv.", "R", "n reg.", "QRS bias", "QRS bias reg.", "QT bias"]
            + ["QT bias reg.", "QRS", "QT"],
            rows,
        )
    lead_names: dict[str, int] = {}
    for m in meta:
        for k, name in enumerate(m["sig_name"]):
            lead_names[f"L{k}:{name}"] = lead_names.get(f"L{k}:{name}", 0) + 1
    lines += [
        "",
        "## Derivaciones presentes",
        "",
        ", ".join(
            f"{k} ×{v}" for k, v in sorted(lead_names.items(), key=lambda kv: (kv[0][:2], -kv[1]))
        ),
        "",
        "## Notas",
        "",
    ]
    lines += [f"- {n}" for n in (*extra_notes, *_notes(beats, meta, config))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


#: Salidas que dependen de qué se corrió (`production`, registros con MLII).
OPTIONAL_OUTPUTS = ("production_blocks.csv", "production_parity.csv", "verdict_mlii.csv")
#: Paquetes de los que dependen los números. La versión sale de los metadatos del paquete
#: instalado (`importlib.metadata`), no de `__version__`: el wheel de PyWavelets 1.9.0
#: todavía dice `pywt.__version__ == "1.8.0"`.
VERSIONED_PACKAGES = ("neurokit2", "numpy", "scipy", "pandas", "PyWavelets", "wfdb")


def package_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out = {}
    for name in VERSIONED_PACKAGES:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "no instalado"
    return out


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    key = df["label"].map(_label_key)
    return df.assign(_k=key).sort_values(["lead", "rpeaks", "_k"], ascending=[True, False, True])


def write_all(
    beats: pd.DataFrame,
    meta: list[dict[str, Any]],
    config: ReportConfig,
    out: Path,
    extra_notes: list[str] | None = None,
) -> pd.DataFrame:
    """Métricas, robustez, veredicto e informe. Devuelve el veredicto."""
    # Los archivos que solo salen con `production` o con MLII se borran antes: si no, una
    # corrida sin ellos deja los de la anterior al lado de un `summary.md` que no les
    # corresponde.
    for stale in OPTIONAL_OUTPUTS:
        (out / stale).unlink(missing_ok=True)
    metrics = build_metrics(beats, meta, config)
    metrics.to_csv(out / "metrics.csv", index=False)
    rob = robustness(beats, meta, config)
    rob.to_csv(out / "robustness.csv", index=False)
    verdict = verdict_table(metrics, rob)
    verdict.to_csv(out / "verdict.csv", index=False)
    mlii = None
    sub = beats[beats["lead_name"] == "MLII"]
    if len(sub):
        mlii = verdict_table(build_metrics(sub, meta, config))
        mlii["n_rec"] = [int(sub[sub["lead"] == lead]["record"].nunique()) for lead in mlii["lead"]]
        mlii.to_csv(out / "verdict_mlii.csv", index=False)
    prod_blocks = production_blocks(beats, meta, config)
    parity = production_parity(beats, meta, config)
    if len(prod_blocks):
        prod_blocks.to_csv(out / "production_blocks.csv", index=False)
    if len(parity):
        parity.to_csv(out / "production_parity.csv", index=False)
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "verdict": verdict.to_dict(orient="records"),
                "robustness": rob.to_dict(orient="records"),
                "production_blocks": prod_blocks.to_dict(orient="records"),
                "production_parity": parity.to_dict(orient="records"),
                "config": asdict(config),
                "versions": package_versions(),
            },
            indent=1,
            default=str,
        )
    )
    write_summary(
        out / "summary.md",
        metrics,
        verdict,
        rob,
        beats,
        meta,
        config,
        extra_notes or [],
        mlii,
        prod_blocks=prod_blocks,
        parity=parity,
    )
    return verdict
