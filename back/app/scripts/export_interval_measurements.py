"""Exporta a CSV las mediciones de intervalos por bloque (`ecg_interval_measurement`).

Es la única forma de leer esa tabla: ninguna API la expone, porque el QTc del
delineador no sigue al del cardiólogo lo suficiente como para mostrarlo como
número del paciente (`tools/physionet/README.md`). Es el dato del chaleco real
que la tesis analiza contra lo validado en la QT Database.

Una fila por bloque medido, ordenadas por estudio y por posición en el
registro. **No sale ningún dato del paciente**: el `study_id` es la única
referencia, y cruzarlo con una persona exige la base (Ley 25.326). `start_s` y
`duration_s` son tiempo de registro —muestras sobre la frecuencia del estudio—,
no hora de pared: un estudio con huecos de grabación los tiene comprimidos.

Tampoco sale **ninguna fecha**. El `created_at` de la fila se escribe en la
pasada del motor, minutos después de grabado el bloque: exportarlo dejaba en el
CSV el día y la hora en que se monitoreó a cada paciente, y con la agenda del
servicio eso alcanza para reidentificarlo sin tocar la base. Ningún análisis de
la tesis lo necesita: la posición es `start_s` y la versión `model_version`.
Los estudios de pacientes dados de baja (`patient.deleted_at`) no se exportan.

    python -m app.scripts.export_interval_measurements [--study <uuid>] [--out archivo.csv]

Sin `--out` escribe en la salida estándar; sin `--study`, todos los estudios.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import uuid
from collections.abc import Iterable, Sequence
from typing import Any, TextIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ecg_interval_measurement import ECGIntervalMeasurement
from app.db.models.patient import Patient
from app.db.models.study import Study

#: Orden de las columnas del CSV. Cambiarlo rompe los notebooks que lo leen:
#: agregar al final.
COLUMNS: tuple[str, ...] = (
    "study_id",
    "start_sample_index",
    "sample_count",
    "sample_rate",
    "start_s",
    "duration_s",
    "beats",
    "candidate_beats",
    "coverage_ratio",
    "qt_ms",
    "qtc_ms",
    "r_amplitude_mv",
    "heart_rate_bpm",
    "candidate_heart_rate_bpm",
    "qrs_ms",
    "method",
    "experimental",
    "model_version",
    "batch_id",
)

#: Lo que un estudio sin `sample_rate` tiene en el resto del backend.
_DEFAULT_SAMPLE_RATE = 500


async def fetch_rows(db: AsyncSession, study_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Las filas del CSV, ya con los nombres de `COLUMNS`."""
    query = (
        select(ECGIntervalMeasurement, Study.sample_rate)
        .join(Study, Study.id == ECGIntervalMeasurement.study_id)
        .join(Patient, Patient.id == Study.patient_id)
        .where(Patient.deleted_at.is_(None))
        .order_by(ECGIntervalMeasurement.study_id, ECGIntervalMeasurement.start_sample_index)
    )
    if study_id is not None:
        query = query.where(ECGIntervalMeasurement.study_id == study_id)
    rows: list[dict[str, Any]] = []
    for measurement, sample_rate in (await db.execute(query)).all():
        rate = int(sample_rate or _DEFAULT_SAMPLE_RATE)
        rows.append(
            {
                "study_id": measurement.study_id,
                "start_sample_index": measurement.start_sample_index,
                "sample_count": measurement.sample_count,
                "sample_rate": rate,
                "start_s": round(measurement.start_sample_index / rate, 3),
                "duration_s": round(measurement.sample_count / rate, 3),
                "beats": measurement.beats,
                "candidate_beats": measurement.candidate_beats,
                "coverage_ratio": round(measurement.coverage_ratio, 4),
                "qt_ms": round(measurement.qt_ms, 1),
                "qtc_ms": round(measurement.qtc_ms, 1),
                "r_amplitude_mv": round(measurement.r_amplitude_mv, 4),
                "heart_rate_bpm": round(measurement.heart_rate_bpm, 2),
                "candidate_heart_rate_bpm": round(measurement.candidate_heart_rate_bpm, 2),
                "qrs_ms": "" if measurement.qrs_ms is None else round(measurement.qrs_ms, 1),
                "method": measurement.method,
                "experimental": measurement.experimental,
                "model_version": measurement.model_version,
                "batch_id": measurement.batch_id,
            }
        )
    return rows


def write_csv(rows: Iterable[dict[str, Any]], out: TextIO) -> int:
    """Escribe el encabezado y las filas. Devuelve cuántas filas escribió."""
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    count = 0
    for row in rows:
        writer.writerow(row)
        count += 1
    return count


async def _run(study_id: uuid.UUID | None, out_path: str | None) -> int:
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        rows = await fetch_rows(db, study_id)
    if out_path is None:
        return write_csv(rows, sys.stdout)
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        return write_csv(rows, handle)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=uuid.UUID, default=None, help="un solo estudio")
    parser.add_argument("--out", default=None, help="archivo CSV (por omisión, la salida estándar)")
    args = parser.parse_args(argv)
    count = asyncio.run(_run(args.study, args.out))
    # Al error estándar: con la salida estándar redirigida a un archivo, el
    # resumen no puede meterse en el CSV.
    print(f"{count} bloques exportados", file=sys.stderr)


if __name__ == "__main__":
    main()
