"""Busca latidos en los estudios ingeridos antes de que existieran las métricas Holter.

La ingesta analiza cada lote al procesarlo, pero los estudios anteriores tienen
todo su historial sin analizar y `GET /studies/{id}/holter-metrics` los informa
como `pending`. Este script los pone al día desde los segmentos crudos de S3:
no hay que volver a decodificar tramas.

Avanza de a `BEAT_SAMPLES_PER_PASS` muestras y commitea entre pasadas, tomando
la fila del estudio con `FOR UPDATE` como la ingesta. Así un estudio abierto
sigue recibiendo lotes mientras se pone al día. Idempotente: retoma desde el
cursor `beats_analyzed_samples`.

    python -m app.scripts.backfill_beat_analysis [--dry-run] [--study <uuid>]
"""

import argparse
import asyncio
import uuid

from sqlalchemy import func, select

from app.db.models.study import Study, StudyStatus
from app.db.session import async_session_factory
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest.processing import (
    BEAT_SAMPLES_PER_PASS,
    append_beat_analysis,
    compact_beat_chunks,
)


async def _study_ids(study_id: uuid.UUID | None) -> list[uuid.UUID]:
    if study_id is not None:
        return [study_id]
    async with async_session_factory() as db:
        query = (
            select(Study.id)
            .where(
                Study.deleted_at.is_(None),
                func.jsonb_array_length(Study.ecg_segments) > 0,
                Study.beats_analyzed_samples < Study.samples_count,
            )
            .order_by(Study.started_at)
        )
        return list((await db.scalars(query)).all())


async def _backfill(study_id: uuid.UUID, dry_run: bool) -> bool:
    passes = 0
    while True:
        async with async_session_factory() as db:
            study = await repo.get_study_for_update(db, study_id)
            if study is None:
                print(f"{study_id}  no existe")
                return False
            before = study.beats_analyzed_samples
            if dry_run:
                print(f"{study_id}  analizadas={before:>9} de {study.samples_count:>9}  (dry-run)")
                await db.rollback()
                return True
            await append_beat_analysis(db, study, max_samples=BEAT_SAMPLES_PER_PASS)
            closed = study.status is not StudyStatus.IN_PROGRESS
            finished = study.beats_analyzed_samples == before
            if closed and (finished or study.beats_analyzed_samples >= study.samples_count):
                study.ecg_beat_chunks = await asyncio.to_thread(
                    compact_beat_chunks, study, force=True
                )
            await db.commit()
            passes += 1
            if finished or study.beats_analyzed_samples >= study.samples_count:
                print(
                    f"{study_id}  analizadas={study.beats_analyzed_samples:>9}"
                    f" de {study.samples_count:>9}  pasadas={passes}"
                )
                return True


async def _run(dry_run: bool, study_id: uuid.UUID | None) -> None:
    ids = await _study_ids(study_id)
    done = sum([await _backfill(item, dry_run) for item in ids])
    print(f"\nEstudios {'revisados' if dry_run else 'analizados'}: {done} de {len(ids)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="mostrar sin escribir")
    parser.add_argument("--study", type=uuid.UUID, default=None, help="un solo estudio")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run, args.study))


if __name__ == "__main__":
    main()
