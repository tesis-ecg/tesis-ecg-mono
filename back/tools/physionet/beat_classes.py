"""Se y PPV por clase de latido (N, S, V) contra MIT-BIH, con el mapeo AAMI EC57.

    cd back
    uv run --with wfdb python -m tools.physionet.evaluate --beat-classes
    uv run --with wfdb python -m tools.physionet.evaluate --beat-classes --records 100 119 208

Corre el motor **como en producción**: bloques de `ml_analysis_block_seconds`
con su contexto izquierdo y derecho, y el banco de plantillas enhebrado de un
bloque al siguiente. Lo que se mide son las etiquetas que persiste
(`PipelineResult.beat_labels`), no una reimplementación.

Mapeo AAMI EC57 de los símbolos de MIT-BIH:

- N: `N`, `L`, `R`, `e`, `j` (normal, bloqueos de rama y escape).
- S: `A`, `a`, `J`, `S` (supraventricular).
- V: `V`, `E` (ventricular).
- F (fusión) y Q (marcapaso, no clasificable) no entran en ninguna de las tres
  clases; un latido detectado sobre uno se ignora en el PPV de S y V.

**Registros con marcapasos** (102, 104, 107, 217) quedan afuera, como pide EC57:
el espigón no es un latido del paciente y el chaleco no es para portadores.

Un latido detectado se empareja con la anotación más cercana a menos de 150 ms
(`MATCH_SAMPLES`). Se/PPV se calculan sobre latidos, no sobre registros:

- Se = (anotados de la clase que el motor etiquetó con esa clase) / (anotados).
  Un anotado que no se detectó, o que cayó en X o Q, cuenta como perdido.
- PPV = (etiquetados con la clase que son de esa clase) / (etiquetados). Un
  latido etiquetado sin anotación cerca cuenta como falso positivo.

**Nada se ajusta sobre estas etiquetas**: se leen después, para medir.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings  # noqa: E402
from app.ml import beat_labels as bl  # noqa: E402
from app.ml import pipeline  # noqa: E402
from app.ml.rpeak_detection import clean_signal, detect_rpeaks  # noqa: E402
from tools.physionet.evaluate import (  # noqa: E402
    DATA_DIR,
    TARGET_RATE,
    public_data_config,
)

AAMI_N = frozenset("NLRej")
AAMI_S = frozenset("AaJS")
AAMI_V = frozenset("VE")
#: Marcapasos: fuera de la evaluación (EC57).
PACED = ("102", "104", "107", "217")
MATCH_SAMPLES = 75  # 150 ms a 500 Hz

BLOCK_SECONDS = 300.0
CONTEXT_SECONDS = 60.0
LOOKAHEAD_SECONDS = 30.0

#: Las tres clases, con el código de etiqueta del motor.
CLASSES = {"N": bl.CODE_N, "S": bl.CODE_S, "V": bl.CODE_V}


@dataclass(frozen=True)
class Annotated:
    name: str
    signal_mv: np.ndarray
    samples: np.ndarray
    classes: np.ndarray  # 'N', 'S', 'V' o '-' (F, Q y demás: no cuentan)


def load(name: str) -> Annotated:
    import wfdb
    from scipy.signal import resample_poly

    path = str(DATA_DIR / "mitdb" / name)
    record = wfdb.rdrecord(path, channels=[0], physical=True)
    annotation = wfdb.rdann(path, "atr")
    signal = resample_poly(np.asarray(record.p_signal[:, 0], dtype=np.float64), 500, 360)
    symbols = np.asarray(annotation.symbol)
    samples = np.asarray(annotation.sample, dtype=np.int64) * 500 // 360
    mapping = {
        **dict.fromkeys(AAMI_N, "N"),
        **dict.fromkeys(AAMI_S, "S"),
        **dict.fromkeys(AAMI_V, "V"),
    }
    # Todo símbolo que es un latido (no un cambio de ritmo ni una marca).
    beat_symbols = AAMI_N | AAMI_S | AAMI_V | frozenset("F/fQ")
    keep = np.array([symbol in beat_symbols for symbol in symbols])
    return Annotated(
        name=name,
        signal_mv=signal.astype(np.float32),
        samples=samples[keep],
        classes=np.array([mapping.get(symbol, "-") for symbol in symbols[keep]]),
    )


def classify_record(signal: np.ndarray, config: pipeline.PipelineConfig, key: str) -> np.ndarray:
    """Las etiquetas del registro entero, bloque a bloque como en producción."""
    rate = TARGET_RATE
    block = int(BLOCK_SECONDS * rate)
    context = int(CONTEXT_SECONDS * rate)
    lookahead = int(LOOKAHEAD_SECONDS * rate)
    bank = pipeline.empty_bank(config)
    flags = np.zeros(signal.size, dtype=np.uint8)
    parts: list[np.ndarray] = []
    for start in range(0, signal.size, block):
        end = min(start + block, signal.size)
        read_start = max(start - context, 0)
        read_end = min(end + lookahead, signal.size)
        result = pipeline.analyze_batch(
            signal[read_start:read_end],
            flags[read_start:read_end],
            start_sample_index=read_start,
            bank=bank,
            config=config,
            fold_key=f"{key}:{start}",
            context_samples=start - read_start,
            lookahead_samples=read_end - end,
        )
        bank = result.bank
        if result.beat_labels is not None:
            parts.append(result.beat_labels)
    return np.concatenate(parts) if parts else bl.empty_records()


def match(detected: np.ndarray, annotated: np.ndarray) -> np.ndarray:
    """Por latido detectado, el índice de la anotación más cercana a <= 150 ms, o -1."""
    if annotated.size == 0:
        return np.full(detected.size, -1, dtype=np.int64)
    position = np.searchsorted(annotated, detected).clip(1, annotated.size - 1)
    left, right = annotated[position - 1], annotated[position]
    nearest = np.where(np.abs(detected - left) <= np.abs(right - detected), position - 1, position)
    close = np.abs(annotated[nearest] - detected) <= MATCH_SAMPLES
    return np.where(close, nearest, -1)


def score(records: np.ndarray, truth: Annotated) -> dict[str, dict[str, int]]:
    """TP / FP / FN por clase y la detección de R, de un registro."""
    detected = records["sample_index"].astype(np.int64)
    labels = records["label"]
    pointer = match(detected, truth.samples)
    matched_class = np.where(pointer >= 0, truth.classes[np.clip(pointer, 0, None)], "?")
    out: dict[str, dict[str, int]] = {}
    for name, code in CLASSES.items():
        labelled = labels == code
        # Un latido etiquetado cuya anotación no es de ninguna de las tres clases
        # (F, Q) no cuenta ni a favor ni en contra.
        ignored = matched_class == "-"
        true_positive = int((labelled & (matched_class == name)).sum())
        false_positive = int((labelled & ~ignored & (matched_class != name)).sum())
        # El mismo conteo sin los R que el detector inventó (sin anotación cerca):
        # separa lo que falla el clasificador de lo que falla el detector.
        false_positive_real = int(
            (labelled & ~ignored & (matched_class != "?") & (matched_class != name)).sum()
        )
        wanted = np.flatnonzero(truth.classes == name)
        # Los anotados que el motor etiquetó con la clase, por su mejor pareja.
        hit = np.zeros(truth.samples.size, dtype=bool)
        hit[pointer[labelled & (matched_class == name)]] = True
        out[name] = {
            "tp": true_positive,
            "fp": false_positive,
            "fp_real": false_positive_real,
            "fn": int(wanted.size - hit[wanted].sum()),
        }
    # Detección de R: todo anotado que es un latido contra todo R detectado.
    is_beat = np.isin(truth.classes, ["N", "S", "V", "-"])
    reached = np.zeros(truth.samples.size, dtype=bool)
    reached[pointer[pointer >= 0]] = True
    out["R"] = {
        "tp": int(reached[is_beat].sum()),
        "fp": int((pointer < 0).sum()),
        "fp_real": int((pointer < 0).sum()),
        "fn": int(is_beat.sum() - reached[is_beat].sum()),
    }
    # Cuántos latidos detectados quedaron fuera de las tres clases.
    out["unclassified"] = {
        "x": int((labels == bl.CODE_X).sum()),
        "q": int((labels == bl.CODE_Q).sum()),
        "total": int(labels.size),
    }
    return out


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else float("nan")


def _score_record(name: str) -> dict[str, dict[str, int]] | str:
    """Puntúa un registro entero. Devuelve el motivo si no se pudo leer."""
    config = public_data_config()
    config = replace(config, beats=pipeline.build_config(settings, TARGET_RATE).beats)
    try:
        truth = load(name)
    except Exception as error:  # noqa: BLE001 — un registro faltante no corta el resto
        return str(error)
    return score(classify_record(truth.signal_mv, config, name), truth)


def run(records: list[str] | None = None, jobs: int = 1) -> int:
    names = records or sorted(
        path.stem for path in (DATA_DIR / "mitdb").glob("*.hea") if path.stem not in PACED
    )
    config = public_data_config()
    config = replace(config, beats=pipeline.build_config(settings, TARGET_RATE).beats)
    if config.beats is None:
        print("ml_beat_classification_enabled está apagado: no hay nada que evaluar.")
        return 1
    total: dict[str, dict[str, int]] = {}
    print(
        f"{'registro':<9}{'V Se':>8}{'V PPV':>8}{'S Se':>8}{'S PPV':>8}"
        f"{'N Se':>8}{'N PPV':>8}{'X %':>7}{'Q %':>7}{'R Se':>8}{'R PPV':>8}"
    )
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(jobs) as pool:
            results = list(pool.map(_score_record, names))
    else:
        results = [_score_record(name) for name in names]
    for name, scored in zip(names, results, strict=True):
        if isinstance(scored, str):
            print(f"{name:<9}no se pudo leer: {scored}")
            continue
        for key, counts in scored.items():
            bucket = total.setdefault(key, dict.fromkeys(counts, 0))
            for field, value in counts.items():
                bucket[field] += value
        row = scored
        un = row["unclassified"]
        print(
            f"{name:<9}"
            f"{ratio(row['V']['tp'], row['V']['tp'] + row['V']['fn']):>8.3f}"
            f"{ratio(row['V']['tp'], row['V']['tp'] + row['V']['fp']):>8.3f}"
            f"{ratio(row['S']['tp'], row['S']['tp'] + row['S']['fn']):>8.3f}"
            f"{ratio(row['S']['tp'], row['S']['tp'] + row['S']['fp']):>8.3f}"
            f"{ratio(row['N']['tp'], row['N']['tp'] + row['N']['fn']):>8.3f}"
            f"{ratio(row['N']['tp'], row['N']['tp'] + row['N']['fp']):>8.3f}"
            f"{100 * ratio(un['x'], un['total']):>7.1f}{100 * ratio(un['q'], un['total']):>7.1f}"
            f"{ratio(row['R']['tp'], row['R']['tp'] + row['R']['fn']):>8.3f}"
            f"{ratio(row['R']['tp'], row['R']['tp'] + row['R']['fp']):>8.3f}"
        )
    print("\nTOTAL (suma de latidos de todos los registros)")
    for name in ("V", "S", "N", "R"):
        counts = total.get(name)
        if counts is None:
            continue
        print(
            f"  {name}: Se {ratio(counts['tp'], counts['tp'] + counts['fn']):.4f}  "
            f"PPV {ratio(counts['tp'], counts['tp'] + counts['fp']):.4f}  "
            f"PPV sobre latidos reales "
            f"{ratio(counts['tp'], counts['tp'] + counts['fp_real']):.4f}  "
            f"(TP {counts['tp']}, FP {counts['fp']}, de los cuales "
            f"{counts['fp'] - counts['fp_real']} son R que el detector inventó; "
            f"FN {counts['fn']})"
        )
    un = total.get("unclassified", {})
    if un.get("total"):
        print(
            f"  X (ruido) {100 * un['x'] / un['total']:.2f} %, "
            f"Q (no clasificable) {100 * un['q'] / un['total']:.2f} % de {un['total']} latidos"
        )
    print(
        "\nMapeo AAMI EC57: N={N,L,R,e,j} S={A,a,J,S} V={V,E}; sin 102, 104, 107, 217 (marcapasos)."
    )
    return 0


def baseline_detection(names: list[str]) -> tuple[float, float]:
    """Se y PPV de R del detector solo (`detect_rpeaks`), sobre el registro entero.

    La referencia para decir que clasificar no cambió la detección: las etiquetas
    salen de los mismos R. Se mide con la misma regla de emparejamiento.
    """
    tp = fn = fp = 0
    for name in names:
        truth = load(name)
        peaks = detect_rpeaks(clean_signal(truth.signal_mv, TARGET_RATE), TARGET_RATE)
        pointer = match(peaks, truth.samples)
        reached = np.zeros(truth.samples.size, dtype=bool)
        reached[pointer[pointer >= 0]] = True
        tp += int(reached.sum())
        fn += int(truth.samples.size - reached.sum())
        fp += int((pointer < 0).sum())
    return ratio(tp, tp + fn), ratio(tp, tp + fp)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", nargs="*", default=None)
    parser.add_argument("--baseline", action="store_true", help="Se/PPV de R del detector solo")
    parser.add_argument("--jobs", type=int, default=1, help="procesos en paralelo")
    args = parser.parse_args(argv)
    if not (DATA_DIR / "mitdb").exists():
        print("No hay datos de mitdb. Corré evaluate con --download primero.")
        return 1
    names = args.records or sorted(
        path.stem for path in (DATA_DIR / "mitdb").glob("*.hea") if path.stem not in PACED
    )
    status = run(args.records, args.jobs)
    if args.baseline:
        se, ppv = baseline_detection(names)
        print(f"\nDetector de R solo (registro entero): Se {se:.4f}  PPV {ppv:.4f}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
