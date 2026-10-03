"""Corre el motor sobre las capturas del chaleco (canal 2). Ver el README de este directorio.

    cd back
    uv run python -m tools.vest.evaluate
    uv run python -m tools.vest.evaluate captura_canal2_aviso_ll_ra --timeline
    uv run python -m tools.vest.evaluate --mains-hz 0     # sin quitar la red

Cada captura se analiza como **un solo lote** con un banco nuevo: el mismo
`build_config(settings, 500)` + `analyze_batch` de la ingesta, con los flags que
el firmware reportó en la captura. No es idéntico a producción: la ingesta
analiza lote por lote, del largo que mande el chaleco y con el piso de score del
estudio, así que los bordes de lote caen en otro lado. El gate de calidad mira
ventanas de 10 s y no depende de eso salvo en el primer y el último segundo de
cada lote. Al final compara contra las expectativas fijadas en el plan y contra
las guardas de regresión, y sale con código 1 si alguna falla.

Las expectativas **no se ajustan a los resultados**. Si una falla, se investiga
el motor (dónde se quita la red, el Q, los bordes de ventana) y se informa; mover
el número para que pase dejaría a esta herramienta midiendo nada.
"""

from __future__ import annotations

import argparse
import collections
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings  # noqa: E402
from app.db.models.signal_quality import SignalQualityLevel  # noqa: E402
from app.ml.contracts import QualityWindow  # noqa: E402
from app.ml.decompression import (  # noqa: E402
    FLAG_ADC_SATURATED,
    FLAG_LEAD_OFF,
    FLAG_R_PEAK,
    FLAG_RLD_OFF,
    FLAG_SQI_SHIFT,
)
from app.ml.pipeline import analyze_batch, build_config, empty_bank  # noqa: E402
from app.ml.quality import MIN_MAINS_HZ, assess_quality, merge_windows  # noqa: E402
from app.ml.rpeak_detection import (  # noqa: E402
    clean_signal,
    compensate_firmware_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)

SAMPLE_RATE = 500
#: El repo hermano del equipo de Biomédica, al lado de este monorepo.
DEFAULT_CAPTURES = Path(__file__).resolve().parents[4] / "Holter-ECG-System" / "capturas"
PATTERN = "captura_canal2_*.txt"

#: Fondo de escala del canal, en mV: VREF / ganancia del PGA. Son las constantes
#: del firmware (`ADS_VREF_VOLTS = 2.42`, `ADS_PGA_GAIN = 6` en `config.h`), y
#: el `#REGS` de las 16 capturas de canal 2 lo confirma: CONFIG2 = 0xE0 (VREF de
#: 2,42 V) y CH2SET = 0 (ganancia 6).
FULL_SCALE_MV = 2420.0 / 6.0
#: El firmware marca `FLAG_ADC_SATURATED` al 95 % del fondo de escala
#: (`ADC_SATURATION_CODE_THRESHOLD`). La captura no trae el bit: se rehace.
SATURATION_MV = 0.95 * FULL_SCALE_MV
#: Un salto de `n` más largo que esto no es pérdida sino otra referencia (un `n`
#: roto o un reinicio). Mismo valor que `SALTO_N_MAXIMO_S` del repo hermano.
MAX_N_JUMP_SECONDS = 300


# --------------------------------------------------------------------------- #
# Lectura de la captura
# --------------------------------------------------------------------------- #


def load_capture(path: Path) -> tuple[dict[str, np.ndarray], int | None]:
    """Columnas numéricas de la captura, por nombre, y las muestras perdidas.

    El formato es el del logger del firmware: encabezados `#META`, `#FIELDS`,
    etc. (repetidos cada vez que el bridge reabre la ventana) intercalados con
    filas CSV y con líneas de log en texto libre. Solo cuentan las filas que
    tienen tantos campos como el último `#FIELDS` y son todas numéricas.

    El contador `n`, si viene, se lee con las reglas de `tools/banco.py` del
    repo hermano: `n` que no avanza (reinicio) o que salta más de
    `MAX_N_JUMP_SECONDS` es una referencia nueva, no una pérdida; en un salto
    real la fila del salto **también se descarta**, porque si lo perdido fueron
    bytes puede ser media fila pegada a otra con las columnas justas. Las
    pérdidas devueltas son `None` si la captura no trae `n`.

    Los huecos **se cierran**, no se reinsertan: la señal es la concatenación de
    las filas, como la recibiría el motor de un lote sin huecos, y el tiempo
    posterior a un salto queda corrido hacia atrás lo que se perdió.
    """
    fields: list[str] | None = None
    rows: list[list[float]] = []
    lost = 0
    previous_n: int | None = None
    with path.open() as handle:
        for line in handle:
            if line.startswith("#FIELDS"):
                fields = line.strip().split(",")[1:]
                continue
            if line.startswith("#") or fields is None:
                continue
            parts = line.strip().split(",")
            if len(parts) != len(fields):
                continue
            try:
                row = [float(value) for value in parts]
            except ValueError:
                continue
            if "n" in fields:
                sequence = int(row[fields.index("n")])
                jump = 0 if previous_n is None else sequence - previous_n
                previous_n = sequence
                if 1 < jump <= MAX_N_JUMP_SECONDS * SAMPLE_RATE:
                    lost += jump
                    continue
            rows.append(row)
    if fields is None or not rows:
        raise ValueError(f"{path.name}: sin filas de datos")
    data = np.asarray(rows, dtype=np.float64)
    columns = {name: data[:, index] for index, name in enumerate(fields)}
    return columns, (lost if "n" in columns else None)


def build_flags(columns: dict[str, np.ndarray]) -> np.ndarray:
    """Los flags por muestra que el motor recibiría del lote real.

    - `r_lag_ms > 0` es la muestra donde el detector del MCU confirmó un latido
      (`FLAG_R_PEAK`, con su retardo: el motor lo compensa).
    - `LEAD_OFF` es el comparador del AFE **o** el detector por señal
      (`leadoff_susp`). Las 16 capturas traen las dos columnas, pero el firmware
      recién marca el segundo en la trama desde el 30/9
      (`LEADOFF_SIGNAL_MARCA_TRAMA`): las capturas anteriores se evalúan como si
      las hubiera grabado el firmware de hoy.
    - `ADC_SATURATED` se rehace con el umbral del firmware (`SATURATION_MV`):
      la captura no trae el bit.
    - `sqi_level` es el SQI de amplitud del firmware, en los bits de SQI.
    """
    n_samples = len(columns["raw_ch0"])
    flags = np.zeros(n_samples, dtype=np.uint8)
    flags[columns["r_lag_ms"] > 0] |= FLAG_R_PEAK
    lead_off = columns["lead_off"] > 0
    if "leadoff_susp" in columns:
        lead_off |= columns["leadoff_susp"] > 0
    flags[lead_off] |= FLAG_LEAD_OFF
    flags[np.abs(columns["raw_ch0"]) >= SATURATION_MV] |= FLAG_ADC_SATURATED
    flags[columns["rld_off"] > 0] |= FLAG_RLD_OFF
    sqi = columns["sqi_level"].astype(np.uint8) & 0x03
    flags |= (sqi << FLAG_SQI_SHIFT).astype(np.uint8)
    return flags


# --------------------------------------------------------------------------- #
# Corrida del motor
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CaptureResult:
    name: str
    duration_s: float
    lost: int | None
    windows: tuple[QualityWindow, ...]
    findings: collections.Counter[str]
    metrics: dict[str, float]
    #: Falso si las ventanas recalculadas no coinciden con las que devolvió
    #: `analyze_batch`. No debería pasar nunca: es la misma cuenta.
    consistent: bool

    def count(self, level: SignalQualityLevel) -> int:
        return sum(1 for window in self.windows if window.level is level)


def run_capture(path: Path, mains_hz: float | None) -> CaptureResult:
    columns, lost = load_capture(path)
    signal = columns["raw_ch0"].astype(np.float32)
    flags = build_flags(columns)
    run_settings = (
        settings if mains_hz is None else settings.model_copy(update={"ml_mains_hz": mains_hz})
    )
    config = build_config(run_settings, SAMPLE_RATE)
    result = analyze_batch(
        signal,
        flags,
        start_sample_index=0,
        bank=empty_bank(config),
        config=config,
        batch_id=path.stem,
    )

    # `analyze_batch` devuelve las ventanas ya fusionadas. Para el detalle por
    # ventana se repite su primera etapa con los mismos argumentos.
    cleaned = clean_signal(signal, SAMPLE_RATE)
    report = assess_quality(
        signal,
        flags,
        cleaned,
        compensate_firmware_peaks(
            firmware_rpeaks(flags),
            lag_samples=config.quality.firmware_lag_samples,
            refractory_samples=config.quality.firmware_refractory_samples,
        ),
        detect_rpeaks(cleaned, SAMPLE_RATE),
        sample_rate=SAMPLE_RATE,
        thresholds=config.quality,
    )
    recomputed = [
        (item.start_sample, item.length_samples, item.level, item.reason)
        for item in merge_windows(report.windows)
    ]
    returned = [
        (item.start_sample, item.length_samples, item.level, item.reason)
        for item, _ in result.quality_intervals
    ]
    return CaptureResult(
        name=path.stem,
        duration_s=signal.size / SAMPLE_RATE,
        lost=lost,
        windows=report.windows,
        findings=collections.Counter(finding.kind for finding in result.findings),
        metrics=result.metrics,
        consistent=recomputed == returned,
    )


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def _median(values: Sequence[float | None]) -> str:
    present = [value for value in values if value is not None and np.isfinite(value)]
    return f"{float(np.median(present)):.3f}" if present else "-"


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _counter(counter: collections.Counter[str]) -> str:
    return " · ".join(f"{key} {count}" for key, count in counter.most_common()) or "-"


def print_capture(result: CaptureResult, *, timeline: bool) -> None:
    windows = result.windows
    lost = "?" if result.lost is None else str(result.lost)
    print(f"\n=== {result.name}  ({result.duration_s / 60:.1f} min, {lost} muestras perdidas)")
    print(
        f"  ventanas  good {result.count(SignalQualityLevel.GOOD)}"
        f" · marginal {result.count(SignalQualityLevel.MARGINAL)}"
        f" · bad {result.count(SignalQualityLevel.BAD)}   (de {len(windows)})"
    )
    print(f"  motivos   {_counter(collections.Counter(window.reason for window in windows))}")
    print(
        f"  medianas  pSQI {_median([w.psqi for w in windows])}"
        f"  kSQI {_median([w.ksqi for w in windows])}"
        f"  basSQI {_median([w.bassqi for w in windows])}"
        f"  bSQI {_median([w.bsqi for w in windows])}"
    )
    # Cuántas ventanas falla cada índice por sí solo, no solo el primero que
    # falla: es lo que hace falta para saber qué umbral está mordiendo.
    thresholds = build_config(settings, SAMPLE_RATE).quality
    fails = {
        "pSQI": sum(1 for w in windows if w.psqi is not None and w.psqi < thresholds.psqi_min),
        "kSQI": sum(1 for w in windows if w.ksqi is not None and w.ksqi < thresholds.ksqi_min),
        "basSQI": sum(
            1 for w in windows if w.bassqi is not None and w.bassqi < thresholds.bassqi_min
        ),
    }
    print("  fallan    " + " · ".join(f"{name} {count}" for name, count in fails.items()))
    metrics = result.metrics
    print(
        f"  latidos analizados {metrics.get('analyzedBeats', 0):.0f}"
        f" · plantillas {metrics.get('templates', 0):.0f}"
        f" · hallazgos {_counter(result.findings)}"
    )
    if not result.consistent:
        print("  ⚠ las ventanas recalculadas no coinciden con las de analyze_batch")
    if timeline:
        for window in windows:
            start = window.start_sample / SAMPLE_RATE
            end = (window.start_sample + window.length_samples) / SAMPLE_RATE
            print(
                f"    {start:5.0f}-{end:<5.0f}s {window.level.value:<8} {window.reason:<12}"
                f" p={_fmt(window.psqi)} k={_fmt(window.ksqi)} bas={_fmt(window.bassqi, 3)}"
                f" bSQI={_fmt(window.bsqi)}"
            )


# --------------------------------------------------------------------------- #
# Expectativas (plan, Verificación paso 4)
# --------------------------------------------------------------------------- #


Check = Callable[[CaptureResult], tuple[bool, str]]


def at_least_good(minimum: int, total: int) -> Check:
    def check(result: CaptureResult) -> tuple[bool, str]:
        good = result.count(SignalQualityLevel.GOOD)
        detail = f"{good}/{len(result.windows)} good"
        if len(result.windows) != total:
            return False, f"{detail}; se esperaban {total} ventanas"
        return good >= minimum, detail

    return check


def no_good() -> Check:
    def check(result: CaptureResult) -> tuple[bool, str]:
        good = result.count(SignalQualityLevel.GOOD)
        return good == 0, f"{good}/{len(result.windows)} good"

    return check


def no_good_inside(ranges: Sequence[tuple[float, float]]) -> Check:
    """Ninguna ventana `good` que **toque** alguno de los tramos.

    Criterio estricto a propósito: alcanza con que la ventana se superponga un
    instante con el tramo para contarla. Una ventana de 10 s que se lleva aunque
    sea el comienzo de un electrodo que se despega no es una ventana buena.
    """

    def check(result: CaptureResult) -> tuple[bool, str]:
        inside = [
            window
            for window in result.windows
            if any(
                window.start_sample / SAMPLE_RATE < high
                and (window.start_sample + window.length_samples) / SAMPLE_RATE > low
                for low, high in ranges
            )
        ]
        good = [window for window in inside if window.level is SignalQualityLevel.GOOD]
        detail = f"{len(good)}/{len(inside)} good dentro de los tramos"
        if good:
            starts = ", ".join(f"{w.start_sample / SAMPLE_RATE:.0f}s" for w in good)
            detail += f" ({starts})"
        return not good, detail

    return check


def at_most_good(maximum: int) -> Check:
    def check(result: CaptureResult) -> tuple[bool, str]:
        good = result.count(SignalQualityLevel.GOOD)
        return good <= maximum, f"{good}/{len(result.windows)} good"

    return check


def all_good_inside(ranges: Sequence[tuple[float, float]]) -> Check:
    """Todas las ventanas **enteramente** adentro de los tramos son `good`."""

    def check(result: CaptureResult) -> tuple[bool, str]:
        inside = [
            window
            for window in result.windows
            if any(
                window.start_sample / SAMPLE_RATE >= low
                and (window.start_sample + window.length_samples) / SAMPLE_RATE <= high
                for low, high in ranges
            )
        ]
        bad = [window for window in inside if window.level is not SignalQualityLevel.GOOD]
        detail = f"{len(inside) - len(bad)}/{len(inside)} good dentro de los tramos"
        if bad:
            starts = ", ".join(f"{w.start_sample / SAMPLE_RATE:.0f}s" for w in bad)
            detail += f" (no: {starts})"
        return bool(inside) and not bad, detail

    return check


def all_reason(reason: str) -> Check:
    def check(result: CaptureResult) -> tuple[bool, str]:
        matching = sum(1 for window in result.windows if window.reason == reason)
        return matching == len(result.windows), f"{matching}/{len(result.windows)} {reason}"

    return check


EXPECTATIONS: tuple[tuple[str, str, Check], ...] = (
    ("captura_canal2_seco_limpia", "≥ 28/35 good", at_least_good(28, 35)),
    ("captura_canal2_seco_ajustado", "≥ 26/29 good", at_least_good(26, 29)),
    ("captura_canal2_ab_tapa_router", "0 good", no_good()),
    ("captura_canal2_movimiento_con_puente", "0 good", no_good()),
    (
        "captura_canal2_aviso_ll_ra",
        "0 good en 30-95 s y 238-295 s",
        no_good_inside(((30.0, 95.0), (238.0, 295.0))),
    ),
    ("captura_canal2_loff0C_seco_saturada", "todas lead_off", all_reason("lead_off")),
)

#: Guardas de regresión: **no salen del plan** sino de lo que el motor da hoy,
#: revisado ventana por ventana contra la bitácora de cada captura (README de
#: `capturas/`). Fijan las dos direcciones en que un cambio de Q o de umbral
#: puede romper algo sin que ninguna expectativa lo note: abrir ventanas en las
#: capturas ruidosas o cerrar las limpias de `ab_router`.
REGRESSION: tuple[tuple[str, str, Check], ...] = (
    # 160-170 s es la única buena: electrodos puestos pero 12-15 mV de red del
    # router; quedó en el límite y no está contrastada contra nada.
    ("captura_canal2_leadoff_head_con_puente", "≤ 1 good", at_most_good(1)),
    ("captura_canal2_leadoff_final", "≤ 3 good", at_most_good(3)),
    ("captura_canal2_leadoff_piel_cargador", "≤ 12 good", at_most_good(12)),
    # Las posiciones "cerca" del router: 2-4 mV de red, ritmo normal, sin ráfagas.
    (
        "captura_canal2_ab_router",
        "todas good en 130-210 s y 380-450 s",
        all_good_inside(((130.0, 210.0), (380.0, 450.0))),
    ),
)


def print_checks(
    title: str, checks: tuple[tuple[str, str, Check], ...], results: dict[str, CaptureResult]
) -> bool:
    print(f"\n▸ {title}")
    width = max(len(name) for name, _, _ in EXPECTATIONS + REGRESSION)
    passed_all = True
    for name, text, check in checks:
        result = results.get(name)
        if result is None:
            print(f"  {'-':<5} {name:<{width}}  {text:<36}  sin correr")
            continue
        passed, detail = check(result)
        passed_all &= passed
        print(f"  {'PASS' if passed else 'FAIL':<5} {name:<{width}}  {text:<36}  {detail}")
    return passed_all


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "captures",
        nargs="*",
        help="nombres (sin .txt) o rutas; por defecto todas las captura_canal2_*",
    )
    parser.add_argument("--captures-dir", type=Path, default=DEFAULT_CAPTURES)
    parser.add_argument("--timeline", action="store_true", help="una línea por ventana de 10 s")
    parser.add_argument(
        "--mains-hz",
        type=float,
        default=None,
        help=f"pisa ml_mains_hz (hoy {settings.ml_mains_hz:g}); 0 apaga la remoción de red",
    )
    args = parser.parse_args(argv)
    # `model_copy` no corre los validadores de `Settings`: se repite acá el suyo
    # para que un valor sin sentido no termine como "no se pudo leer" en cada captura.
    if args.mains_hz is not None and not (args.mains_hz == 0 or args.mains_hz >= MIN_MAINS_HZ):
        parser.error(f"--mains-hz tiene que ser 0 (apagado) o una red de {MIN_MAINS_HZ:g} Hz o más")

    if args.captures:
        paths = [
            Path(item) if item.endswith(".txt") else args.captures_dir / f"{item}.txt"
            for item in args.captures
        ]
    else:
        paths = sorted(args.captures_dir.glob(PATTERN))
    if not paths:
        print(f"No hay capturas {PATTERN} en {args.captures_dir} (ver tools/vest/README.md).")
        return 1

    mains = settings.ml_mains_hz if args.mains_hz is None else args.mains_hz
    print(f"▸ {len(paths)} capturas · red {mains:g} Hz · {args.captures_dir}")
    results: dict[str, CaptureResult] = {}
    for path in paths:
        try:
            result = run_capture(path, args.mains_hz)
        except (OSError, ValueError) as error:
            print(f"\n=== {path.stem}  no se pudo leer: {error}")
            continue
        results[result.name] = result
        print_capture(result, timeline=args.timeline)

    expectations = print_checks("Expectativas (plan)", EXPECTATIONS, results)
    regression = print_checks("Guardas de regresión", REGRESSION, results)
    return 0 if expectations and regression else 1


if __name__ == "__main__":
    raise SystemExit(main())
