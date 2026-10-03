"""Evalúa el motor de detección contra MIT-BIH. Ver el README de este directorio.

    uv run --with wfdb python -m tools.physionet.evaluate --download
    uv run --with wfdb python -m tools.physionet.evaluate

**Nunca se entrena con estas etiquetas.** El motor es no supervisado; las
anotaciones solo se leen después, para medir. Por eso reportar sobre el mismo
dataset es legítimo acá y no lo sería en un clasificador entrenado.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings  # noqa: E402
from app.ml import morphology, pipeline  # noqa: E402
from app.ml.hrv import build_rr, prematurity  # noqa: E402
from app.ml.rpeak_detection import clean_signal, detect_rpeaks  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
TARGET_RATE = 500
SOURCE_RATE = 360

#: Anotaciones de latido normal en el código de MIT-BIH. Todo lo demás que sea
#: un latido cuenta como no-normal. Los símbolos que no son latidos (cambios de
#: ritmo, artefactos, marcas de calidad) se descartan.
NORMAL_SYMBOLS = frozenset("NLRej")
ABNORMAL_SYMBOLS = frozenset("VASFaJE/fQ")

#: Registros con carga ectópica alta y bajos, para medir las dos direcciones. Un
#: detector que marca todo tendría recall perfecto sobre `208` y sería inútil:
#: los de control existen para que eso se vea.
HIGH_BURDEN = ("208", "119", "233", "221")
LOW_BURDEN = ("100", "101", "103")

#: Etapa 1. `nstdb` toma el registro 118 limpio y le suma ruido REAL de electrodo
#: (deriva de línea de base, artefacto muscular, pérdida de contacto) a una SNR
#: conocida. Es la única forma honesta de medir un gate de calidad: contra ruido
#: gaussiano sintético cualquier umbral parece razonable.
#:
#: El eje es la SNR en dB, de más limpio a más sucio. `_6` es −6 dB: ruido con el
#: doble de amplitud que el ECG. Ahí el gate tiene que rechazar casi todo.
NOISE_SNR = ("24", "18", "12", "06", "00", "_6")
NOISE_BASE = "118"


@dataclass(frozen=True)
class Record:
    name: str
    signal_mv: np.ndarray
    beat_samples: np.ndarray
    beat_is_abnormal: np.ndarray


def download(records: tuple[str, ...] = (*HIGH_BURDEN, *LOW_BURDEN)) -> None:
    """Baja solo lo que falta, registro por registro.

    `dl_database` sobre la base entera son ~100 MB y se corta a la mitad con
    cualquier hipo de red, dejando el directorio a medias — y como existe, un
    reintento lo daría por completo. Por eso se chequea archivo por archivo.
    """
    target = DATA_DIR / "mitdb"
    target.mkdir(parents=True, exist_ok=True)
    pending = [
        f"{name}{ext}"
        for name in records
        for ext in (".hea", ".dat", ".atr")
        if not (target / f"{name}{ext}").exists()
    ]
    if not pending:
        print(f"  mitdb: los {len(records)} registros ya están en {target}")
        return
    _fetch("mitdb", target, pending)


def download_noise(base: str = NOISE_BASE, snrs: tuple[str, ...] = NOISE_SNR) -> None:
    """Ídem para `nstdb`, más el registro limpio que le sirve de referencia."""
    target = DATA_DIR / "nstdb"
    target.mkdir(parents=True, exist_ok=True)
    pending = [
        f"{base}e{snr}{ext}"
        for snr in snrs
        for ext in (".hea", ".dat", ".atr")
        if not (target / f"{base}e{snr}{ext}").exists()
    ]
    if pending:
        _fetch("nstdb", target, pending)
    else:
        print(f"  nstdb: los {len(snrs)} niveles de SNR ya están en {target}")
    # La referencia limpia sale de mitdb: es el MISMO registro, sin ruido sumado.
    download((base,))


def _fetch(db: str, target: Path, pending: list[str]) -> None:
    """Baja archivo por archivo, tolerando que uno falle.

    PhysioNet devuelve 502 con cierta frecuencia. Un archivo que no baja no puede
    tirar abajo la descarga entera: se reporta y se sigue, y el próximo
    `--download` lo reintenta —- por eso el chequeo de arriba es por archivo.
    """
    import wfdb

    print(f"  bajando {len(pending)} archivos a {target}")
    for name in pending:
        try:
            wfdb.dl_files(db, str(target), [name])
        except Exception as error:  # noqa: BLE001
            print(f"    ✗ {name}: {error}")


def load_record(name: str, base: str = "mitdb") -> Record:
    """Lee un registro y lo lleva a 500 Hz, la frecuencia de nuestro hardware.

    `resample_poly(500, 360)` es la razón exacta 25/18: remuestreo polifásico sin
    interpolación aproximada, que sobre un QRS de 90 ms importa.
    """
    import wfdb
    from scipy.signal import resample_poly

    path = str(DATA_DIR / base / name)
    record = wfdb.rdrecord(path, channels=[0], physical=True)  # mV, ya en la unidad nuestra
    annotation = wfdb.rdann(path, "atr")

    signal = np.asarray(record.p_signal[:, 0], dtype=np.float64)
    resampled = resample_poly(signal, TARGET_RATE, SOURCE_RATE)

    symbols = np.array(annotation.symbol)
    samples = np.asarray(annotation.sample, dtype=np.int64) * TARGET_RATE // SOURCE_RATE
    is_beat = np.array(
        [symbol in NORMAL_SYMBOLS or symbol in ABNORMAL_SYMBOLS for symbol in symbols]
    )
    return Record(
        name=name,
        signal_mv=resampled.astype(np.float32),
        beat_samples=samples[is_beat],
        beat_is_abnormal=np.array(
            [symbol in ABNORMAL_SYMBOLS for symbol in symbols[is_beat]], dtype=bool
        ),
    )


def public_data_config() -> pipeline.PipelineConfig:
    """Config con las reglas que solo valen para NUESTRO hardware apagadas.

    `flatline` y `saturated` son propiedades del AFE DC-acoplado del chaleco. Los
    registros públicos vienen AC-acoplados y ya centrados: dejarlos prendidos
    mediría un artefacto del formato del archivo y no la señal.
    """
    config = pipeline.build_config(settings, TARGET_RATE)
    # MIT-BIH y nstdb se grabaron en Boston: la red es de 60 Hz, no los 50 del chaleco.
    return replace(
        config, quality=replace(config.quality, flatline_mv=0.0, bassqi_min=0.0, mains_hz=60.0)
    )


def evaluate(record: Record, config: pipeline.PipelineConfig) -> dict[str, float]:
    flags = np.zeros(record.signal_mv.size, dtype=np.uint8)  # sin R-peaks del firmware
    result = pipeline.analyze_batch(
        record.signal_mv,
        flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        fold_key=record.name,
    )

    # Se recalculan las asignaciones para poder medir la PUREZA de cada cluster,
    # que es lo que valida el método: `analyze_batch` no las devuelve porque
    # ningún consumidor de producción las necesita.
    cleaned = clean_signal(record.signal_mv, TARGET_RATE)
    peaks = detect_rpeaks(cleaned, TARGET_RATE)
    quality_mask = np.zeros(record.signal_mv.size, dtype=bool)
    for interval, _ in result.quality_intervals:
        if interval.level.value == "good":
            end = interval.start_sample + interval.length_samples
            quality_mask[interval.start_sample : end] = True
    beats = morphology.extract_beats(cleaned, peaks, quality_mask, TARGET_RATE)
    bank, assignment = morphology.assign_and_update(
        pipeline.empty_bank(config),
        beats,
        match_threshold=config.match_threshold,
        max_templates=config.max_templates,
    )
    scores = morphology.anomaly_score(
        morphology.dissimilarity_to_dominant(bank, beats),
        prematurity(build_rr(peaks, quality_mask, TARGET_RATE))[beats.beat_index],
        match_threshold=config.match_threshold,
    )

    # Cada latido detectado se etiqueta con la anotación de referencia más cercana.
    truth = np.zeros(beats.n_beats, dtype=bool)
    if record.beat_samples.size and beats.n_beats:
        nearest = np.searchsorted(record.beat_samples, beats.rpeaks).clip(
            0, record.beat_samples.size - 1
        )
        for index, candidate in enumerate(nearest):
            peak = int(beats.rpeaks[index])
            options = [c for c in (candidate - 1, candidate) if 0 <= c < record.beat_samples.size]
            best = min(options, key=lambda c: abs(int(record.beat_samples[c]) - peak))
            # ±150 ms: la anotación de referencia marca el pico R y nuestro
            # detector también, pero cada uno con su propia latencia de filtro.
            if abs(int(record.beat_samples[best]) - peak) <= 75:
                truth[index] = bool(record.beat_is_abnormal[best])

    dominant = morphology.dominant_template(bank)
    non_dominant = (assignment.cluster_ids != (dominant.cluster_id if dominant else -1)) & (
        assignment.cluster_ids >= 0
    )

    abnormal_total = int(truth.sum())
    flagged = scores >= config.anomaly_score_min
    hours = record.signal_mv.size / TARGET_RATE / 3600

    purity = float(truth[non_dominant].mean()) if non_dominant.any() else float("nan")
    recall = float(truth[flagged].sum() / abnormal_total) if abnormal_total else float("nan")
    precision = float(truth[flagged].mean()) if flagged.any() else float("nan")

    episodes = sum(1 for f in result.findings if f.kind == "morphology_anomaly")
    return {
        "latidos": float(beats.n_beats),
        "no_normales": float(abnormal_total),
        "carga_%": 100.0 * abnormal_total / max(beats.n_beats, 1),
        "recall": recall,
        "precision": precision,
        "pureza_clusters": purity,
        "clusters": float(len(bank.templates)),
        "hallazgos/h": episodes / max(hours, 1e-6),
        "analizable_%": 100.0 * float(quality_mask.mean()),
    }


def noisy_mask(n_samples: int) -> np.ndarray:
    """Dónde `nstdb` sumó ruido, según su protocolo (Moody 1984).

    El ruido **no** cubre el registro entero: los primeros 5 min quedan limpios y
    a partir de ahí se alternan bloques de 2 min con ruido y 2 min sin. Sobre 30
    min eso da 43,5 % contaminado, que es el techo de lo que un gate correcto
    puede rechazar — sin esto, el `% analizable` estancado en ~58 % se leería
    como que el gate satura, cuando en realidad está acertando.
    """
    seconds = np.arange(n_samples) / TARGET_RATE
    return (seconds >= 300) & (((seconds - 300) // 120).astype(int) % 2 == 0)


def quality_profile(
    record: Record, config: pipeline.PipelineConfig, *, with_truth: bool = False
) -> dict[str, float | str]:
    """Etapa 1 sola: qué fracción del registro deja pasar el gate, y por qué."""
    flags = np.zeros(record.signal_mv.size, dtype=np.uint8)
    result = pipeline.analyze_batch(
        record.signal_mv,
        flags,
        start_sample_index=0,
        bank=pipeline.empty_bank(config),
        config=config,
        fold_key=record.name,
    )

    total = float(record.signal_mv.size)
    por_nivel: dict[str, float] = {"good": 0.0, "marginal": 0.0, "bad": 0.0, "unknown": 0.0}
    por_razon: dict[str, float] = {}
    rechazado = np.zeros(record.signal_mv.size, dtype=bool)
    for interval, _ in result.quality_intervals:
        por_nivel[interval.level.value] += interval.length_samples
        if interval.level.value != "good":
            end = interval.start_sample + interval.length_samples
            rechazado[interval.start_sample : end] = True
            por_razon[interval.reason] = (
                por_razon.get(interval.reason, 0.0) + interval.length_samples
            )

    peor = max(por_razon.items(), key=lambda item: item[1])[0] if por_razon else "—"
    profile: dict[str, float | str] = {
        "bueno_%": 100.0 * por_nivel["good"] / total,
        "malo_%": 100.0 * por_nivel["bad"] / total,
        "razon": peor,
    }
    if with_truth:
        # Lo que importa no es cuánto rechaza sino DÓNDE: un gate que tira el
        # 42 % al azar da el mismo porcentaje y es inservible.
        ruido = noisy_mask(record.signal_mv.size)
        aciertos = float((rechazado & ruido).sum())
        profile["sensib"] = aciertos / max(float(ruido.sum()), 1.0)
        profile["precis"] = aciertos / max(float(rechazado.sum()), 1.0)
    return profile


def run_stage1(base: str = NOISE_BASE, snrs: tuple[str, ...] = NOISE_SNR) -> None:
    """Tabla de `% analizable` contra SNR. La forma de la curva es el resultado.

    Un gate útil tiene que ser **monótono**: cuanto peor la SNR, menos señal
    aprueba. Pero el `% rechazado` solo no alcanza —- uno que descarte el 42 % al
    azar daría el mismo número—, así que se contrasta contra `noisy_mask`:
    `sensib` es cuánto del ruido real atrapó y `precis` cuánto de lo que tiró
    estaba efectivamente contaminado.
    """
    config = public_data_config()
    columnas = ["bueno_%", "malo_%", "sensib", "precis", "razon"]
    print(f"{'registro':<12}{'SNR (dB)':>10}" + "".join(f"{c:>12}" for c in columnas))
    print("-" * (22 + 12 * len(columnas)))

    filas: list[tuple[str, str, str]] = [(base, "limpio", "mitdb")]
    filas += [(f"{base}e{snr}", snr.replace("_", "−"), "nstdb") for snr in snrs]
    for nombre, etiqueta, carpeta in filas:
        try:
            record = load_record(nombre, base=carpeta)
        except Exception as error:  # noqa: BLE001
            print(f"{nombre:<12}{etiqueta:>10}  no se pudo leer: {error}")
            continue
        # El registro limpio no tiene bloques de ruido contra los cuales medir:
        # ahí el resultado es el `% bueno` a secas, que debe dar casi 100.
        perfil = quality_profile(record, config, with_truth=carpeta == "nstdb")
        celdas = "".join(
            f"{perfil[c]:>12.3f}"
            if isinstance(perfil.get(c), float)
            else f"{perfil.get(c, '—'):>12}"
            for c in columnas
        )
        print(f"{nombre:<12}{etiqueta:>10}{celdas}")
    print(
        f"\n`nstdb` contamina el {100 * noisy_mask(TARGET_RATE * 1800).mean():.1f} % del registro "
        "(5 min limpios y después bloques alternados de 2 min):\nese es el techo de `malo_%`, "
        "no un límite del gate."
    )


def main() -> int:
    if "--qtdb" in sys.argv[1:]:
        # Benchmark de delineación contra la QT Database: tiene su propia CLI (ver README).
        from tools.physionet import qtdb

        return qtdb.main([arg for arg in sys.argv[1:] if arg != "--qtdb"])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--records", nargs="*", default=None)
    parser.add_argument("--qtdb", action="store_true", help="delineación vs. QTDB (ver README)")
    parser.add_argument(
        "--stage1",
        action="store_true",
        help="evalúa solo el gate de calidad contra nstdb (ruido real a SNR conocida)",
    )
    args = parser.parse_args()

    if args.download:
        print("▸ Descargando de PhysioNet")
        download()
        if args.stage1:
            download_noise()
        print()

    if args.stage1:
        if not (DATA_DIR / "nstdb").exists():
            print("No hay datos de nstdb. Corré con --download --stage1 primero.")
            return 1
        print("▸ Etapa 1 — gate de calidad contra ruido real de electrodo\n")
        run_stage1()
        return 0

    if not (DATA_DIR / "mitdb").exists():
        print("No hay datos. Corré con --download primero (ver tools/physionet/README.md).")
        return 1

    names = args.records or [*HIGH_BURDEN, *LOW_BURDEN]
    config = public_data_config()
    columns = [
        "latidos",
        "no_normales",
        "carga_%",
        "recall",
        "precision",
        "pureza_clusters",
        "clusters",
        "hallazgos/h",
        "analizable_%",
    ]
    print(f"{'registro':<10}" + "".join(f"{name:>17}" for name in columns))
    print("-" * (10 + 17 * len(columns)))
    for name in names:
        try:
            record = load_record(name)
        except Exception as error:  # noqa: BLE001 — un registro faltante no corta el resto
            print(f"{name:<10} no se pudo leer: {error}")
            continue
        metrics = evaluate(record, config)
        etiqueta = f"{name}{'*' if name in LOW_BURDEN else ''}"
        print(f"{etiqueta:<10}" + "".join(f"{metrics[c]:>17.3f}" for c in columns))
    print("\n* control de carga baja: ahí lo que importa es `hallazgos/h`, no el recall.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
