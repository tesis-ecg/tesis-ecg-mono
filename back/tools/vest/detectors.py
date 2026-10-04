"""Se y PPV de los dos detectores de R sobre las capturas del chaleco (canal 2).

    cd back
    uv run python -m tools.vest.detectors
    uv run python -m tools.physionet.evaluate --detectors --part vest   # lo mismo, en paralelo

Es la parte `vest` del benchmark de `tools/physionet/detectors.py`; los números y el
método están en `tools/physionet/DETECTORS.md`. La captura se lee con el parser de
`tools.vest.evaluate` (`load_capture`, `build_flags`), el mismo de la evaluación del
motor.

No hay anotación de un cardiólogo: la referencia es el detector del **firmware**, el
`FLAG_R_PEAK` de cada muestra, y se mide solo donde el gate del motor dice GOOD (el
mismo `assess_quality` que corre `tools.vest.evaluate`). Dos versiones del tren:

* `nominal` — el de producción: `firmware_rpeaks` + `compensate_firmware_peaks` con el
  retardo fijo de `ml_firmware_peak_lag_ms` (250 ms) y el refractario de 300 ms.
* `por_latido` — el mismo tren sin dobletes, pero corrido por el retardo que el
  firmware informa en cada latido: `r_lag_ms` (pico → confirmación, en el dominio de la
  señal de diagnóstico) más `fir_delay` del `#META` (el FIR de 161 taps, 80 muestras).
  Es como alinea los latidos `tools/promediar_latidos.py` del repo hermano.

**¿El gate sesga a favor de `nk`?** En principio podría: `nk` es el `detected_peaks` del
gate, y una ventana que pasa la Capa A y los índices espectrales queda MARGINAL si el
bSQI entre el firmware y `nk` no llega a 0,80. `pt` no participa. Por eso se mide
también en la región `GOOD sin bSQI`: el mismo `assess_quality` con el tren del
firmware como `detected_peaks`, que da bSQI = 1 en toda ventana con latidos del
firmware. Esa región es Capa A + línea plana + índices espectrales + "el firmware vio
algún latido": no depende de ningún candidato por construcción. Si coincide con GOOD
(y MARGINAL es 0 %), el bSQI no excluyó ninguna ventana y no hubo sesgo.

**Variantes**, en la misma región:

* `nk_por_bloque` — `detect_nk_blocked`: `nk` como corre el motor, por bloques de
  300 s con 60 s de contexto y 30 s de lookahead.
* `pt_por_lote` — `detect_pt_batched`: como corre en producción, por lotes de 26 s
  con 30 s de contexto a cada lado, reaprendiendo el umbral en cada uno.
* `pt_reinicio` — `detect_pt_rearmed`: sobre la captura entera, vuelve a arrancar
  tras 5 s sin latidos (no está en producción).
* `pt_sin_marcas` — `pt` sobre la señal con las muestras marcadas `LEAD_OFF` o
  `ADC_SATURATED` (y 0,5 s alrededor) reemplazadas por una recta entre los bordes: lo
  que haría una guarda por flags antes de `analyze_window` (no está en producción).
"""

from __future__ import annotations

import argparse
import collections
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings  # noqa: E402
from app.ml.decompression import FLAG_ADC_SATURATED, FLAG_LEAD_OFF  # noqa: E402
from app.ml.pipeline import build_config  # noqa: E402
from app.ml.quality import assess_quality  # noqa: E402
from app.ml.rpeak_detection import (  # noqa: E402
    clean_signal,
    compensate_firmware_peaks,
    detect_r_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)
from tools.physionet.detectors import (  # noqa: E402
    DETECTORS,
    add,
    detect_nk_blocked,
    detect_pt_batched,
    detect_pt_rearmed,
    edge_region,
    fmt_pair,
    head,
    row,
    score_all,
)
from tools.vest.evaluate import (  # noqa: E402
    DEFAULT_CAPTURES,
    PATTERN,
    SAMPLE_RATE,
    build_flags,
    load_capture,
)

#: El retardo del FIR de diagnóstico si la captura no trae `#META` (161 taps).
DEFAULT_FIR_DELAY = 80
REFERENCES = ("nominal", "por_latido")
#: `good_sin_bsqi` = GOOD del gate corrido con el firmware contra sí mismo (bSQI ≡ 1).
SCOPES = ("good", "marginal", "good_sin_bsqi")
SCOPE_LABELS = {"good": "GOOD", "marginal": "MARGINAL", "good_sin_bsqi": "GOOD sin bSQI"}
FLAGGED = FLAG_LEAD_OFF | FLAG_ADC_SATURATED
#: Margen alrededor de cada muestra marcada que `pt_sin_marcas` también reemplaza.
MASK_MARGIN_S = 0.5
VEST_VARIANTS = ("nk_por_bloque", "pt_por_lote", "pt_reinicio", "pt_sin_marcas")


def fir_delay_samples(path: Path) -> int:
    """`fir_delay` del primer `#META` de la captura: `diag[k]` es `raw[k − fir_delay]`."""
    with path.open() as handle:
        for line in handle:
            if line.startswith("#META"):
                for field in line.strip().split(",")[1:]:
                    key, _, value = field.partition("=")
                    if key == "fir_delay" and value.isdigit():
                        return int(value)
                break
    return DEFAULT_FIR_DELAY


def per_beat_peaks(
    flags: np.ndarray, lag_ms: np.ndarray, *, fir_delay: int, refractory_samples: int
) -> np.ndarray:
    """El tren del firmware llevado al pico con el retardo que informa cada latido.

    Los dobletes se descartan **antes**, sobre las muestras de confirmación y con el
    mismo refractario que producción (`compensate_firmware_peaks` con retardo 0): así
    los dos trenes de referencia tienen exactamente los mismos latidos y solo difieren
    en dónde los ubican.
    """
    marks = compensate_firmware_peaks(
        firmware_rpeaks(flags), lag_samples=0, refractory_samples=refractory_samples
    )
    peaks = marks - np.round(lag_ms[marks] * SAMPLE_RATE / 1000.0).astype(np.int64) - fir_delay
    return np.sort(peaks[peaks >= 0])


def without_flagged(signal: np.ndarray, flags: np.ndarray, rate: int) -> np.ndarray:
    """La señal con lo marcado (`LEAD_OFF`, `ADC_SATURATED`, ±0,5 s) reemplazado por una recta.

    Una recta entre las muestras sanas de los bordes y no una constante: un valor fijo
    pondría un escalón en cada borde, y un escalón es justamente lo que ciega a `pt`.
    """
    flagged = (flags & FLAGGED) != 0
    if not flagged.any():
        return signal
    margin = int(MASK_MARGIN_S * rate)
    mask = np.convolve(flagged.astype(np.float32), np.ones(2 * margin + 1), mode="same") > 0
    keep = np.flatnonzero(~mask)
    if keep.size < 2:
        return np.zeros_like(signal)
    out = signal.astype(np.float64)
    out[mask] = np.interp(np.flatnonzero(mask), keep, out[keep])
    return out.astype(signal.dtype)


def score_capture(path: Path) -> dict[str, Any]:
    columns, lost = load_capture(path)
    signal = columns["raw_ch0"].astype(np.float32)
    flags = build_flags(columns)
    config = build_config(settings, SAMPLE_RATE)
    thresholds = config.quality

    cleaned = clean_signal(signal, SAMPLE_RATE)
    nominal = compensate_firmware_peaks(
        firmware_rpeaks(flags),
        lag_samples=thresholds.firmware_lag_samples,
        refractory_samples=thresholds.firmware_refractory_samples,
    )
    references = {
        "nominal": nominal,
        "por_latido": per_beat_peaks(
            flags,
            columns["r_lag_ms"],
            fir_delay=fir_delay_samples(path),
            refractory_samples=thresholds.firmware_refractory_samples,
        ),
    }
    # `nk` es exactamente lo que el gate recibe como `detected_peaks`: se calcula una vez.
    detections = {
        "nk": detect_rpeaks(cleaned, SAMPLE_RATE),
        "pt": detect_r_peaks(signal, SAMPLE_RATE),
    }
    assert set(detections) == set(DETECTORS)
    detections["nk_por_bloque"] = detect_nk_blocked(signal, SAMPLE_RATE)
    detections["pt_por_lote"] = detect_pt_batched(signal, SAMPLE_RATE)
    detections["pt_reinicio"] = detect_pt_rearmed(signal, SAMPLE_RATE)
    detections["pt_sin_marcas"] = detect_r_peaks(
        without_flagged(signal, flags, SAMPLE_RATE), SAMPLE_RATE
    )
    assert set(detections) == {*DETECTORS, *VEST_VARIANTS}

    def gate(detected: np.ndarray) -> Any:
        return assess_quality(
            signal,
            flags,
            cleaned,
            nominal,
            detected,
            sample_rate=SAMPLE_RATE,
            thresholds=thresholds,
        )

    masks = {scope: np.zeros(signal.size, dtype=bool) for scope in SCOPES}
    # El gate de producción (`nk`) y el mismo gate con el firmware contra sí mismo.
    production = gate(detections["nk"])
    for report, levels in (
        (production, {"good": "good", "marginal": "marginal"}),
        (gate(nominal), {"good": "good_sin_bsqi"}),
    ):
        for window in report.windows:
            scope = levels.get(window.level.value)
            if scope is not None:
                end = window.start_sample + window.length_samples
                masks[scope][window.start_sample : end] = True
    edge = edge_region(signal.size, SAMPLE_RATE)
    regions = {scope: mask & edge for scope, mask in masks.items()}

    scores: dict[str, dict[str, Any]] = {scope: {} for scope in SCOPES}
    for name, reference in references.items():
        reference = reference[reference < signal.size]
        for scope, by_detector in score_all(reference, detections, regions, SAMPLE_RATE).items():
            scores[scope][name] = by_detector
    return {
        "part": "vest",
        "record": path.stem,
        "duration_s": signal.size / SAMPLE_RATE,
        "lost": lost,
        "fraction": {scope: float(mask.mean()) for scope, mask in masks.items()},
        "same_good": bool(np.array_equal(masks["good"], masks["good_sin_bsqi"])),
        "windows": dict(collections.Counter(window.level.value for window in production.windows)),
        # Fracción de muestras marcadas LEAD_OFF o ADC_SATURATED. Agrupa las capturas,
        # pero no es la causa: lo que ciega a `pt` es cualquier evento de energía grande
        # —el riel del AFE o un escalón de línea de base, marcado o no— que deja `spki`
        # arriba (ver DETECTORS.md).
        "flagged": float(((flags & FLAGGED) != 0).mean()),
        "scores": scores,
    }


def report(results: list[dict[str, Any]]) -> list[str]:
    rows = sorted(results, key=lambda item: item["record"])
    out = ["## Capturas del chaleco (canal 2) contra el firmware", ""]
    out.append(
        "Se / PPV dentro de las ventanas GOOD del gate. `nominal` = tren de producción"
        " (retardo fijo de 250 ms); `por latido` = `r_lag_ms` + `fir_delay` de cada latido."
        " `GOOD sin bSQI` = el gate con el firmware contra sí mismo, que no depende de"
        " ningún candidato. `marcadas` = muestras con `LEAD_OFF` o `ADC_SATURATED`."
    )
    out += [""] + head(
        *("captura", "min", "good %", "marginal %", "good sin bSQI %", "marcadas %"),
        *("latidos ref", "nominal ±75 nk", "nominal ±75 pt", "por latido ±50 nk"),
        "por latido ±50 pt",
    )
    for item in rows:
        good = item["scores"]["good"]
        counts = good["nominal"]["nk"]["75"]
        cells = [
            item["record"].removeprefix("captura_canal2_"),
            f"{item['duration_s'] / 60:.1f}",
            *(f"{100 * item['fraction'][scope]:.0f}" for scope in SCOPES),
            f"{100 * item['flagged']:.0f}",
            str(counts["tp"] + counts["fn"]),
        ]
        cells += [fmt_pair(good["nominal"][key]["75"]) for key in DETECTORS]
        cells += [fmt_pair(good["por_latido"][key]["50"]) for key in DETECTORS]
        out.append(row(cells))

    total_s = sum(item["duration_s"] for item in rows)
    fractions = {
        scope: sum(item["fraction"][scope] * item["duration_s"] for item in rows) / total_s
        for scope in SCOPES
    }
    same = sum(item["same_good"] for item in rows)
    out += [
        "",
        f"{len(rows)} capturas, {total_s / 60:.1f} min: GOOD {100 * fractions['good']:.1f} %,"
        f" MARGINAL {100 * fractions['marginal']:.1f} %, GOOD sin bSQI"
        f" {100 * fractions['good_sin_bsqi']:.1f} % del tiempo. La región GOOD es idéntica,"
        f" muestra a muestra, a la de GOOD sin bSQI en {same} de {len(rows)} capturas.",
        "",
        "### Totales brutos",
        "",
        *head(
            *("<capturas", "<región", "<referencia", "tol", "latidos ref"),
            *("nk Se / PPV", "pt Se / PPV", "nk desvío ms", "pt desvío ms"),
        ),
    ]
    clean = [item for item in rows if item["flagged"] == 0]
    flagged = [item for item in rows if item["flagged"] > 0]
    groups = [
        (f"todas ({len(rows)})", rows),
        (f"sin marcas ({len(clean)})", clean),
        (f"con marcas ({len(flagged)})", flagged),
    ]
    blocks = [
        (groups[0], "good", REFERENCES),
        (groups[0], "marginal", ("por_latido",)),
        (groups[0], "good_sin_bsqi", ("por_latido",)),
        (groups[1], "good", ("por_latido",)),
        (groups[2], "good", ("por_latido",)),
    ]
    for (label, chosen), scope, references in blocks:
        for reference in references:
            for tol in ("75", "50"):
                out.append(row(_totals(label, chosen, scope, reference, tol)))
    out += [
        "",
        "El desvío (detección − referencia, a ±75 ms) es la mediana de las medianas por captura.",
        "",
        "### Variantes",
        "",
        "GOOD, referencia por latido, ±75 ms. `nk_por_bloque` y `pt_por_lote` son los dos"
        " detectores como corren en producción (bloques de 300 s; lotes de 26 s con 30 s de"
        " contexto a cada lado); `pt_reinicio` vuelve a arrancar tras 5 s sin latidos;"
        " `pt_sin_marcas` corre sobre la señal con lo marcado (±0,5 s) reemplazado por una"
        " recta. Las dos últimas no están en producción.",
        "",
    ]
    keys = ("nk", "nk_por_bloque", "pt", *VEST_VARIANTS[1:])
    out += head("<capturas", "latidos ref", *keys)
    for label, chosen in groups + [
        (item["record"].removeprefix("captura_canal2_"), [item])
        for item in rows
        if item["flagged"] > 0
    ]:
        totals = {
            key: add(item["scores"]["good"]["por_latido"][key]["75"] for item in chosen)
            for key in keys
        }
        beats = totals["nk"]["tp"] + totals["nk"]["fn"]
        if beats:
            out.append(row([label, str(beats), *(fmt_pair(totals[key]) for key in keys)]))
    return out


def _totals(
    label: str, rows: list[dict[str, Any]], scope: str, reference: str, tol: str
) -> list[str]:
    totals = {
        key: add(item["scores"][scope][reference][key][tol] for item in rows) for key in DETECTORS
    }
    offsets = {
        key: _median_offset(item["scores"][scope][reference][key]["75"] for item in rows)
        for key in DETECTORS
    }
    return [
        label,
        SCOPE_LABELS[scope],
        reference.replace("_", " "),
        f"±{tol} ms",
        str(totals["nk"]["tp"] + totals["nk"]["fn"]),
        *(fmt_pair(totals[key]) for key in DETECTORS),
        *(offsets[key] if tol == "75" else "" for key in DETECTORS),
    ]


def _median_offset(items: Iterable[dict[str, Any]]) -> str:
    medians = [counts["offset_ms"][1] for counts in items if counts.get("offset_ms")]
    return f"{float(np.median(medians)):+.0f}" if medians else "—"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("captures", nargs="*", help="nombres (sin .txt); por defecto todas")
    parser.add_argument("--captures-dir", type=Path, default=DEFAULT_CAPTURES)
    args = parser.parse_args(argv)
    if args.captures:
        paths = [args.captures_dir / f"{name}.txt" for name in args.captures]
    else:
        paths = sorted(args.captures_dir.glob(PATTERN))
    results = []
    for path in paths:
        try:
            results.append(score_capture(path))
        except (OSError, ValueError) as error:
            print(f"  {path.stem}: no se pudo leer: {error}")
    if not results:
        print(f"No hay capturas {PATTERN} en {args.captures_dir}.")
        return 1
    print("\n".join(report(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
