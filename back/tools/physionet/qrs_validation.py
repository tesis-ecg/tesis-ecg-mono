"""Ancho del QRS y PR del motor contra las marcas manuales de la QT Database.

    cd back
    uv run --with wfdb python -m tools.physionet.evaluate --qrs
    uv run --with wfdb python -m tools.physionet.evaluate --pr --lead 1
    uv run --with wfdb python -m tools.physionet.evaluate --qrs --pr

Mide `app/ml/qrs.py` (inicio y fin del QRS) y `app/ml/conduction.py` (inicio de
la P, con el PR = inicio de P → inicio de QRS) sobre los mismos registros y la
misma réplica de producción que el benchmark de QT (`qtdb.py`): 250 Hz →
500 Hz, `clean_signal` y `detect_rpeaks` de la ingesta, una derivación por vez.
La señal de análisis es la de `pipeline._amplitude_signal` sin el notch (no hay
red en QTDB): pasaaltos de 0,5 Hz, sin pasabajos.

Las marcas son las del primer cardiólogo (`q1c`): `(` antes del latido es el
inicio del QRS, `)` después es el fin; `(` antes de una `p` es el inicio de la P.

**Gate** (el de la Fase 3 del plan de QT): |sesgo| <= 20 ms en el ancho del QRS,
por latido y por mediana de registro. Si no pasa, `qrs.QRS_WIDTH_VALIDATED` se
apaga y el ancho deja de clasificar. Para el PR se informa el sesgo, el error
absoluto y la cobertura (qué fracción de los latidos con P anotada tiene PR).

**Qué se ajustó con estas etiquetas.** Cuatro constantes: `SLOPE_FRACTION`,
`NOISE_FLOOR_FACTOR` y `EDGE_EXTENSION_MS` del QRS, y `P_ONSET_FRACTION` y
`P_ONSET_SHIFT_MS` del PR (la última compensa el suavizado del filtro de P). Se
eligieron entre un puñado de valores mirando este sesgo, así que el sesgo de
acá es **optimista**: no es una validación independiente. El resto de los
umbrales (120 ms, 200 ms, 120 ms de PR corto) vienen de las guías AHA/ACC/HRS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ml import conduction, qrs  # noqa: E402
from app.ml.rpeak_detection import clean_signal, detect_rpeaks  # noqa: E402
from tools.physionet import qtdb  # noqa: E402

#: Cuánto puede distar el R detectado de la marca del cardiólogo, en muestras de 500 Hz.
MATCH_SAMPLES = 30
#: Gate de sesgo del ancho, en ms.
GATE_MS = 20.0


def _highpass(signal: np.ndarray) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    sos = butter(5, 0.5, btype="highpass", output="sos", fs=500.0)
    return np.asarray(sosfiltfilt(sos, signal.astype(np.float64)), dtype=np.float32)


def _p_onsets(record: str, annotated_beats: list[int]) -> dict[int, int]:
    """Inicio de la P (muestras de 250 Hz) del latido anotado `k`, para los que la tienen."""
    import wfdb

    annotation = wfdb.rdann(str(qtdb.DATA_DIR / record), "q1c")
    symbols, samples = list(annotation.symbol), np.asarray(annotation.sample)
    onsets: dict[int, int] = {}
    for position, index in enumerate(annotated_beats):
        previous = annotated_beats[position - 1] if position else -1
        for j in range(index - 1, previous, -1):
            if symbols[j] == "p" and j > 0 and symbols[j - 1] == "(":
                onsets[position] = int(samples[j - 1])
                break
    return onsets


def evaluate(lead: int, *, do_qrs: bool, do_pr: bool) -> dict[str, float]:
    import wfdb
    from scipy.signal import resample_poly

    width_errors: list[np.ndarray] = []
    onset_errors: list[np.ndarray] = []
    offset_errors: list[np.ndarray] = []
    record_biases: list[float] = []
    pr_errors: list[np.ndarray] = []
    pr_record_biases: list[float] = []
    annotated_with_p = detected_p = candidates = 0
    for record in qtdb.available_records():
        table = qtdb.parse_q1c(record)
        if table.empty:
            continue
        annotation = wfdb.rdann(str(qtdb.DATA_DIR / record), "q1c")
        beat_positions = [
            i for i, symbol in enumerate(annotation.symbol) if symbol in qtdb.BEAT_SYMBOLS
        ]
        p_on = _p_onsets(record, beat_positions)
        table = table.assign(p_on=[p_on.get(int(k), np.nan) for k in table["beat"]])
        table = table[table["qrs_on"].notna() & table["qrs_off"].notna()]
        if len(table) < 5:
            continue
        raw = wfdb.rdrecord(str(qtdb.DATA_DIR / record), channels=[lead], physical=True)
        signal = resample_poly(np.asarray(raw.p_signal[:, 0], dtype=float), 2, 1)
        low = max(int(table["qrs_on"].min() * 2) - 5000, 0)
        high = min(int(table["qrs_off"].max() * 2) + 5000, signal.size)
        segment = signal[low:high].astype(np.float32)
        cleaned = clean_signal(segment, 500)
        try:
            peaks = detect_rpeaks(cleaned, 500)
        except Exception:  # noqa: BLE001 — un registro que el detector no soporta no corta el resto
            continue
        if peaks.size < 5:
            continue
        analyzable = np.ones(segment.size, dtype=bool)
        delineation = qrs.delineate_qrs(_highpass(segment), peaks, analyzable, 500)
        p_waves = conduction.measure_p_waves(
            cleaned, peaks, delineation, np.ones(peaks.size, dtype=bool), 500
        )
        pr = p_waves.pr_ms(delineation, 500)

        reference = (table["r_ann"].to_numpy() * 2 - low).astype(int)
        nearest = np.searchsorted(peaks, reference).clip(0, peaks.size - 1)
        options = np.stack([(nearest - 1).clip(0), nearest], axis=1)
        pick = options[
            np.arange(reference.size),
            np.argmin(np.abs(peaks[options] - reference[:, None]), axis=1),
        ]
        matched = np.abs(peaks[pick] - reference) <= MATCH_SAMPLES

        if do_qrs:
            usable = matched & delineation.valid[pick]
            on_ref = table["qrs_on"].to_numpy() * 2 - low
            off_ref = table["qrs_off"].to_numpy() * 2 - low
            on_error = (peaks[pick] + delineation.onset[pick] - on_ref)[usable] * 2.0
            off_error = (peaks[pick] + delineation.offset[pick] - off_ref)[usable] * 2.0
            onset_errors.append(on_error)
            offset_errors.append(off_error)
            width_errors.append(off_error - on_error)
            if usable.sum() > 3:
                record_biases.append(float(np.median(off_error - on_error)))
        if do_pr:
            reference_pr = (table["qrs_on"].to_numpy() - table["p_on"].to_numpy()) * 4.0
            normal = table["beat_type"].to_numpy() == "N"
            has_p = matched & np.isfinite(reference_pr) & normal
            found = has_p & np.isfinite(pr[pick])
            annotated_with_p += int(has_p.sum())
            detected_p += int(found.sum())
            candidates += int((matched & ~np.isfinite(reference_pr) & normal).sum())
            error = (pr[pick] - reference_pr)[found]
            pr_errors.append(error)
            if found.sum() > 3:
                pr_record_biases.append(float(np.median(error)))

    out: dict[str, float] = {}
    if do_qrs:
        width = np.concatenate(width_errors)
        out.update(
            qrs_beats=float(width.size),
            qrs_width_bias_ms=float(width.mean()),
            qrs_width_mae_ms=float(np.abs(width).mean()),
            qrs_onset_bias_ms=float(np.concatenate(onset_errors).mean()),
            qrs_offset_bias_ms=float(np.concatenate(offset_errors).mean()),
            qrs_record_median_bias_ms=float(np.median(record_biases)),
        )
    if do_pr:
        error = np.concatenate(pr_errors)
        out.update(
            pr_beats=float(error.size),
            pr_coverage=detected_p / max(annotated_with_p, 1),
            pr_bias_ms=float(error.mean()),
            pr_mae_ms=float(np.abs(error).mean()),
            pr_record_median_bias_ms=float(np.median(pr_record_biases)),
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrs", action="store_true", help="ancho del QRS")
    parser.add_argument("--pr", action="store_true", help="PR")
    parser.add_argument("--lead", type=int, nargs="*", default=[0, 1])
    args = parser.parse_args(argv)
    if not (args.qrs or args.pr):
        parser.error("pedí --qrs, --pr o los dos")
    if not qtdb.available_records():
        print("No hay datos de qtdb. Corré `evaluate --qtdb --download` primero.")
        return 1
    status = 0
    for lead in args.lead:
        result = evaluate(lead, do_qrs=args.qrs, do_pr=args.pr)
        print(f"\nDerivación {lead}")
        for key, value in result.items():
            print(f"  {key:<28}{value:>10.2f}")
        if args.qrs:
            ok = (
                abs(result["qrs_width_bias_ms"]) <= GATE_MS
                and abs(result["qrs_record_median_bias_ms"]) <= GATE_MS
            )
            print(f"  gate de ancho (|sesgo| <= {GATE_MS:.0f} ms): {'PASA' if ok else 'NO PASA'}")
            status |= 0 if ok else 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
