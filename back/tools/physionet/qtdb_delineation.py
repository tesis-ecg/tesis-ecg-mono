"""Delineación por latido para el benchmark de QTDB. Ver `qtdb.py` y el README.

Envuelve los delineadores de NeuroKit 0.2.13 (`dwt`, `cwt`, `peak`, `prominence`) para
que la salida quede **alineada 1 a 1 con los R** que se le pasan, y define cómo se
convierte esa salida en intervalos (QRS, QT, QTc, amplitud R) con sus guardas. También
tiene el hueco `production`, que corre `app.ml.intervals` si se puede importar, para
medir el módulo que se despliega con la misma vara que los métodos de stock.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from types import ModuleType
from typing import Any

import numpy as np

TARGET_RATE = 500

#: Columnas de NeuroKit que se extraen por latido (nombre corto -> clave de NeuroKit).
WAVE_KEYS = {
    "r_on": "ECG_R_Onsets",
    "r_off": "ECG_R_Offsets",
    "q": "ECG_Q_Peaks",
    "s": "ECG_S_Peaks",
    "t_peak": "ECG_T_Peaks",
    "t_off": "ECG_T_Offsets",
}

#: Definiciones de intervalo: (inicio, fin) en nombres cortos. `plan` es la del plan
#: (QRS = R_on→R_off, QT = R_on→T_off). `qs` (Q_peak→S_peak, Q_peak→T_off) es la única
#: que admite `peak`, que no da R_on/R_off; para los demás métodos es una variante.
DEFINITIONS = {
    "plan": {"qrs": ("r_on", "r_off"), "qt": ("r_on", "t_off")},
    "qs": {"qrs": ("q", "s"), "qt": ("q", "t_off")},
}

STOCK_METHODS = ("dwt", "cwt", "peak", "prominence")
PRODUCTION = "production"
#: Variantes `método~opción` -> kwargs públicos de `nk.ecg_delineate`. `prominence~rb200`
#: sube `max_r_basepoint_interval` de 100 a 200 ms: con el default,
#: `scipy.signal.peak_prominences(wlen=100 ms)` deja R_on y R_off a ≤ 50 ms del R y el QRS
#: R_on→R_off nunca pasa de 100 ms. Se encontró mirando QTDB: requiere justificación.
VARIANT_METHODS: dict[str, tuple[str, dict[str, Any]]] = {
    "prominence~rb200": ("prominence", {"max_r_basepoint_interval": 200}),
}

#: Rango fisiológico del filtro `plaus`. El del QT es la guarda que producción necesita: un
#: latido perdido por el detector deja un QT > 1 s aunque T_off caiga antes del R siguiente
#: del tren (sele0203, sel853 en la derivación 1).
QRS_PLAUSIBLE_MS = (40.0, 200.0)
QT_PLAUSIBLE_MS = (200.0, 650.0)


def known_method(method: str) -> bool:
    return method in STOCK_METHODS or method in VARIANT_METHODS or method == PRODUCTION


def definitions_for(method: str, has_plan: bool, variants: bool) -> list[str]:
    """Definiciones que se evalúan para un método.

    `plan` siempre que el método dé R_on y R_off. `qs` para `peak` (es su única forma de
    dar un QRS) y, con `--variants`, para el resto de los métodos de stock.
    """
    out = ["plan"] if has_plan else []
    if method == "peak" or (variants and method in STOCK_METHODS):
        out.append("qs")
    return out


def method_label(method: str, definition: str) -> str:
    return method if definition == "plan" else f"{method}[Q→S; Q→Toff]"


# --------------------------------------------------------------------------- NeuroKit
class _CachedPywt:
    """Proxy de `pywt` que memoiza `cwt` sobre el MISMO array (identidad de objeto).

    En NeuroKit 0.2.13 `_find_tppeaks` recalcula la CWT de toda la señal una vez por
    intervalo RR (≈ 300 veces por bloque de 5 min) y `_onset_offset_delineator` otras
    tres. El resultado es idéntico (mismo array, mismos argumentos; NeuroKit no muta ni la
    señal ni la matriz); solo cambia el tiempo de cómputo. `--no-cwt-cache` lo apaga.
    """

    def __init__(self, real: ModuleType) -> None:
        self._real = real
        self._ref: object | None = None
        self._key: tuple[Any, ...] | None = None
        self._val: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def cwt(
        self, data: Any, scales: Any, wavelet: Any, sampling_period: float = 1.0, **kw: Any
    ) -> Any:
        key = (
            np.asarray(scales).tobytes(),
            str(wavelet),
            float(sampling_period),
            tuple(sorted(kw.items())),
        )
        if self._ref is data and self._key == key:
            return self._val
        val = self._real.cwt(data, scales, wavelet, sampling_period=sampling_period, **kw)
        self._ref, self._key, self._val = data, key, val
        return val


def _delineate_module(cwt_cache: bool) -> ModuleType:
    # `neurokit2.ecg.ecg_delineate` como atributo es la FUNCIÓN pública (el `__init__` la
    # reexporta con el mismo nombre); el módulo con los despachadores internos se pide así.
    module = importlib.import_module("neurokit2.ecg.ecg_delineate")
    current = getattr(module, "pywt")  # noqa: B009 — el módulo no tiene stubs
    cached = isinstance(current, _CachedPywt)
    if cwt_cache and not cached:
        setattr(module, "pywt", _CachedPywt(current))  # noqa: B010
    elif not cwt_cache and cached:
        setattr(module, "pywt", current._real)  # noqa: B010
    return module


def delineate_aligned(
    cleaned: np.ndarray, rpeaks: np.ndarray, method: str, *, cwt_cache: bool = True
) -> tuple[dict[str, np.ndarray], int]:
    """`nk.ecg_delineate` con los arrays alineados 1 a 1 con `rpeaks`.

    La función pública de NeuroKit 0.2.13 hace `[x for x in values if x > 0 or isnan(x)]`
    sobre cada onda: un valor ≤ 0 se DESCARTA en vez de volverse NaN y corre todos los
    latidos siguientes un lugar (17 corridas en QTDB, todas de `peak`). Acá se llama al
    mismo despachador interno, se aplica el mismo `≥ len -> NaN` y los ≤ 0 pasan a NaN.
    Devuelve también cuántas claves habría desalineado la API pública, para documentarlo.

    `cwt` devuelve P y T con n − 1 valores, un par por intervalo (R_i, R_i+1): la T del
    intervalo i es del latido i (se rellena al final) y la P es del latido i + 1 (se
    rellena al principio; la P no entra en las métricas).
    """
    base, options = VARIANT_METHODS.get(method, (method, {}))
    module = _delineate_module(cwt_cache)
    ecg = np.asarray(cleaned, dtype=np.float64)
    rp = np.asarray(rpeaks, dtype=np.int64)
    rate = TARGET_RATE
    if base == "dwt":
        waves = module._dwt_ecg_delineator(ecg, rp, sampling_rate=rate)
    elif base == "cwt":
        waves = module._ecg_delineator_cwt(ecg, rpeaks=rp, sampling_rate=rate)
    elif base == "peak":
        waves = module._ecg_delineator_peak(ecg, rpeaks=rp, sampling_rate=rate)
    elif base == "prominence":
        waves = module._prominence_ecg_delineator(ecg, rpeaks=rp, sampling_rate=rate, **options)
    else:
        raise ValueError(f"método desconocido: {method}")
    out: dict[str, np.ndarray] = {}
    misaligned = 0
    for key, values in waves.items():
        arr = np.array([np.nan if v is None else v for v in list(values)], dtype=np.float64)
        if np.any(arr[np.isfinite(arr)] <= 0):
            misaligned += 1
        arr[(arr <= 0) | (arr >= ecg.size)] = np.nan
        if arr.size < rp.size:
            pad = np.full(rp.size - arr.size, np.nan)
            arr = np.concatenate([pad, arr] if key.startswith("ECG_P_") else [arr, pad])
        out[key] = arr[: rp.size]
    return out, misaligned


# --------------------------------------------------------------------------- bloque
def block_stats(
    waves: Mapping[str, np.ndarray],
    train: np.ndarray,
    sig: np.ndarray,
    sig_hp: np.ndarray,
    *,
    next_r_guard: bool = True,
) -> dict[str, dict[str, Any]]:
    """Lo que reportaría producción para el bloque: mediana sobre TODOS los latidos del tren.

    Producción no sabe qué latidos anotó el cardiólogo: delinea el bloque entero y reporta
    la mediana de los latidos válidos si hay ≥ 30. Acá se calcula eso mismo, por definición
    y por filtro, para compararlo con la mediana manual del registro.

    Guardas por latido (filtro `valid`): QRS con marcas finitas y R_on ≤ R ≤ R_off; QT con
    R_on ≤ R < T_off < R siguiente del tren (una T que termina después del próximo QRS es
    imposible: `cwt` la pone en el QRS siguiente y, con huecos en el tren, a segundos). El
    filtro `plaus` agrega los rangos de `QRS_PLAUSIBLE_MS` / `QT_PLAUSIBLE_MS`.
    """
    n = train.size
    r = train.astype(np.float64)
    rr = np.full(n, np.nan)
    rr[1:] = np.diff(r) / TARGET_RATE
    r_next = np.full(n, np.inf)
    if next_r_guard:
        r_next[:-1] = r[1:]

    def arr(short: str) -> np.ndarray:
        a = waves.get(WAVE_KEYS[short])
        return np.full(n, np.nan) if a is None else np.asarray(a, dtype=np.float64)

    out: dict[str, dict[str, Any]] = {}
    for definition, d in DEFINITIONS.items():
        qa, qb = (arr(k) for k in d["qrs"])
        ta, tb = (arr(k) for k in d["qt"])
        with np.errstate(invalid="ignore"):
            qrs = (qb - qa) * 1000.0 / TARGET_RATE
            qt = (tb - ta) * 1000.0 / TARGET_RATE
            ok_qrs = np.isfinite(qrs) & (qa <= r) & (r <= qb)
            ok_qt = np.isfinite(qt) & (ta <= r) & (tb > r) & (tb < r_next)
            qtc = qt / np.cbrt(rr)
            ok_qtc = ok_qt & np.isfinite(rr)
            plaus_qt = (qt >= QT_PLAUSIBLE_MS[0]) & (qt <= QT_PLAUSIBLE_MS[1])
            plaus_qrs = (qrs >= QRS_PLAUSIBLE_MS[0]) & (qrs <= QRS_PLAUSIBLE_MS[1])
        on = arr("r_on")
        good_on = np.isfinite(on) & (on >= 0) & (on < sig.size)
        ramp = np.full(n, np.nan)
        ramp_hp = np.full(n, np.nan)
        on_idx = on[good_on].astype(np.int64)
        ramp[good_on] = sig[train[good_on]] - sig[on_idx]
        ramp_hp[good_on] = sig_hp[train[good_on]] - sig_hp[on_idx]
        filters = {
            "valid": (ok_qrs, ok_qt, ok_qtc),
            "plaus": (ok_qrs & plaus_qrs, ok_qt & plaus_qt, ok_qtc & plaus_qt),
        }
        for filt, (mq, mt, mc) in filters.items():
            out[f"{definition}/{filt}"] = {
                "n_train": int(n),
                "n_qrs": int(mq.sum()),
                "n_qt": int(mt.sum()),
                "n_qtc": int(mc.sum()),
                "qrs": _median(qrs, mq),
                "qt": _median(qt, mt),
                "qtc": _median(qtc, mc),
                "ramp": _median(ramp, mq & np.isfinite(ramp)),
                "ramp_hp": _median(ramp_hp, mq & np.isfinite(ramp_hp)),
            }
    return out


def _median(values: np.ndarray, mask: np.ndarray) -> float | None:
    return float(np.median(values[mask])) if mask.any() else None


# --------------------------------------------------------------------------- producción
#: Red de cada registro para el notch de `quality.remove_mains`, como la configuraría
#: producción en cada sitio: los `sele*` vienen de la European ST-T Database (50 Hz) y el
#: resto de bases grabadas en Boston (MIT-BIH y BIH, 60 Hz), igual que en `evaluate.py`.
EUROPEAN_PREFIX = "sele"


def mains_hz_for(record: str) -> float:
    return 50.0 if record.startswith(EUROPEAN_PREFIX) else 60.0


#: Columnas por latido de `production`: la medición y cada control del módulo por separado
#: (`BeatIntervals`), para auditar por qué sale cada latido sin copiar su lógica.
PRODUCTION_MASKS = (
    "candidate",
    "ordered",
    "qt_ok",
    "rr_ok",
    "peak_ok",
    "t_contained",
    "qrs_positive",
    "dominant_ok",
    "valid",
)
PRODUCTION_VALUES = ("qt", "qtc", "ramp")


def production_status() -> str | None:
    """None si `app.ml.intervals` se puede importar con lo que usa el harness; si no, el motivo."""
    needed = {
        "app.ml.intervals": ("measure_intervals", "measure_beats", "IntervalThresholds"),
        "app.ml.quality": ("remove_mains",),
    }
    for name, attributes in needed.items():
        try:
            module = importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 — puede estar a medio escribir
            return f"{name}: {type(error).__name__}: {error}"
        missing = [a for a in attributes if not hasattr(module, a)]
        if missing:
            return f"{name} no expone {', '.join(missing)}"
    return None


def production_thresholds(overrides: Mapping[str, Any]) -> Any:
    """`IntervalThresholds` del módulo con `overrides` (los defaults si está vacío)."""
    from app.ml.intervals import IntervalThresholds

    return IntervalThresholds(**dict(overrides))


def production_amplitude_signal(hp: np.ndarray, mains_hz: float) -> np.ndarray:
    """`raw_for_amplitude` con la convención de `measure_intervals`: sin línea de base ni red,
    **sin pasabajos**.

    La línea de base la saca `hp`, el pasaaltos de 0,5 Hz que es la primera etapa de
    `nk.ecg_clean` (`sosfiltfilt`); la red, `quality.remove_mains` (notch `filtfilt` en
    `mains_hz` y sus armónicas). Los dos son de fase cero, así que los R_onset que salen de
    la señal limpia caen en la misma muestra sobre esta.
    """
    from app.ml.quality import remove_mains

    return np.asarray(remove_mains(hp, TARGET_RATE, mains_hz), dtype=np.float64)


def measure_production(
    cleaned: np.ndarray,
    hp: np.ndarray,
    rpeaks: np.ndarray,
    *,
    mains_hz: float,
    overrides: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, dict[str, Any]]]:
    """Corre el módulo que se despliega (`app.ml.intervals`) sobre el bloque.

    Con los mismos R que los métodos de stock, la señal limpia de producción para
    delinear, `production_amplitude_signal(hp, mains_hz)` como `raw_for_amplitude`, todo el
    bloque como GOOD (QTDB no pasa por el gate de calidad), sin `dominant` (QTDB no tiene
    plantillas) y `IntervalThresholds(**overrides)`. Devuelve:

    * por latido, alineado con `rpeaks`: `prod_qt`, `prod_qtc`, `prod_ramp` y una columna
      `prod_<control>` por cada máscara de `BeatIntervals`. El módulo no expone R_on / T_off,
      así que estas columnas reemplazan a las marcas en las métricas por latido, y su
      `valid` reemplaza al del harness;
    * la mediana de bloque de `measure_intervals`, lo que se guarda en la base, con su
      `candidate_beats` / `coverage_ratio` / FC. La amplitud R va a `ramp_hp` porque se mide
      sobre la señal sin pasabajos. Los límites de RR son los que expone el módulo.
    """
    from app.ml.intervals import measure_beats, measure_intervals

    raw = production_amplitude_signal(hp, mains_hz)
    good = np.ones(cleaned.size, dtype=bool)
    thresholds = production_thresholds(overrides)
    rate = TARGET_RATE
    per_beat: dict[str, np.ndarray] = {
        **{f"prod_{k}": np.zeros(rpeaks.size, dtype=bool) for k in PRODUCTION_MASKS},
        **{f"prod_{k}": np.full(rpeaks.size, np.nan) for k in PRODUCTION_VALUES},
    }
    rr_floor: float | None = None
    rr_cap: float | None = None
    beats = measure_beats(cleaned, raw, rpeaks, good, rate, thresholds)
    if beats is not None and beats.rpeaks.size:
        # `measure_beats` sanea el tren (sin la muestra 0 ni R contiguos): se vuelve a
        # alinear por posición.
        pos = np.clip(np.searchsorted(beats.rpeaks, rpeaks), 0, beats.rpeaks.size - 1)
        hit = beats.rpeaks[pos] == rpeaks
        for name in PRODUCTION_MASKS:
            per_beat[f"prod_{name}"][hit] = getattr(beats, name)[pos[hit]]
        per_beat["prod_qt"][hit] = beats.qt_ms[pos[hit]]
        per_beat["prod_qtc"][hit] = beats.qtc_ms[pos[hit]]
        per_beat["prod_ramp"][hit] = beats.r_amplitude_mv[pos[hit]]
        rr_floor, rr_cap = float(beats.rr_floor_s), float(beats.rr_cap_s)
    block = measure_intervals(cleaned, raw, rpeaks, good, rate, thresholds)
    n = int(block.beats) if block is not None else 0
    stats = {
        "n_train": int(rpeaks.size),
        "n_qrs": n,
        "n_qt": n,
        "n_qtc": n,
        "qrs": None if block is None or block.qrs_ms is None else float(block.qrs_ms),
        "qt": None if block is None else float(block.qt_ms),
        "qtc": None if block is None else float(block.qtc_ms),
        "ramp": None,
        "ramp_hp": None if block is None else float(block.r_amplitude_mv),
        "candidate_beats": None if block is None else int(block.candidate_beats),
        "coverage_ratio": None if block is None else float(block.coverage_ratio),
        "heart_rate_bpm": None if block is None else float(block.heart_rate_bpm),
        "candidate_heart_rate_bpm": (
            None if block is None else float(block.candidate_heart_rate_bpm)
        ),
        "experimental": None if block is None else bool(block.experimental),
        "rr_floor_s": rr_floor,
        "rr_cap_s": rr_cap,
        "qt_min_ms": float(thresholds.qt_min_ms),
        "qt_max_ms": float(thresholds.qt_max_ms),
        "mains_hz": float(mains_hz),
    }
    return per_beat, {"plan/valid": dict(stats), "plan/plaus": dict(stats)}
