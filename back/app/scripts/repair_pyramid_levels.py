"""Repara la metadata de la pirámide de los estudios que quedaron congelados.

`append_level_chunks` mutaba en el lugar los dicts de la columna JSONB y
SQLAlchemy dejaba de emitir el UPDATE en cuanto el estudio tenía los seis
niveles (pasadas las 16.384 muestras). Los chunks de cada lote sí se siguieron
escribiendo en S3, así que alcanza con listarlos para rearmar la metadata; no
hay que volver a decodificar ni filtrar nada.

Toma la fila del estudio con `FOR UPDATE`, igual que la ingesta, y si lo que hay
en S3 no cubre exactamente lo procesado no escribe ese estudio. Idempotente.

    python -m app.scripts.repair_pyramid_levels [--dry-run] [--study <uuid>]
"""

import argparse
import asyncio
import uuid
from typing import Any

from sqlalchemy import func, select

from app.db.models.study import Study, StudyStatus
from app.db.session import async_session_factory
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest.processing import (
    BASE_BUCKET,
    compact_pyramid,
    rebuild_level_metadata,
)


def _coverage(levels: list[dict[str, Any]]) -> int:
    """Muestras que cubre el nivel base de la pirámide."""
    base = next((lv for lv in levels if int(lv["samplesPerBucket"]) == BASE_BUCKET), None)
    return 0 if base is None else int(base["pointCount"]) // 2 * BASE_BUCKET


async def _study_ids(study_id: uuid.UUID | None) -> list[uuid.UUID]:
    if study_id is not None:
        return [study_id]
    async with async_session_factory() as db:
        query = (
            select(Study.id)
            .where(Study.deleted_at.is_(None), func.jsonb_array_length(Study.ecg_segments) > 0)
            .order_by(Study.started_at)
        )
        return list((await db.scalars(query)).all())


async def _repair(study_id: uuid.UUID, dry_run: bool) -> bool:
    async with async_session_factory() as db:
        study = await repo.get_study_for_update(db, study_id)
        if study is None:
            print(f"{study_id}  no existe")
            return False

        views = [(False, "cruda", study.samples_count)]
        if study.filter_view_enabled:
            views.append((True, "filtrada", study.filtered_samples_count))

        rebuilt: dict[bool, list[dict[str, Any]]] = {}
        for filtered, name, processed in views:
            current = (
                study.ecg_filtered_pyramid_levels if filtered else study.ecg_pyramid_levels
            ) or []
            try:
                levels = await asyncio.to_thread(rebuild_level_metadata, study, filtered=filtered)
            except ValueError as error:
                print(f"{study_id}  {name:<8} NO SE REPARA: {error}")
                await db.rollback()
                return False
            print(
                f"{study_id}  {name:<8} procesadas={processed:>9}"
                f"  pirámide antes={_coverage(current):>9}  después={_coverage(levels):>9}"
                + ("  (dry-run)" if dry_run else "")
            )
            rebuilt[filtered] = levels

        if dry_run:
            await db.rollback()
            return True

        force = study.status is not StudyStatus.IN_PROGRESS
        study.ecg_pyramid_levels = rebuilt[False]
        study.ecg_pyramid_levels = await asyncio.to_thread(compact_pyramid, study, force=force)
        if True in rebuilt:
            study.ecg_filtered_pyramid_levels = rebuilt[True]
            study.ecg_filtered_pyramid_levels = await asyncio.to_thread(
                compact_pyramid, study, force=force, filtered=True
            )
        await db.commit()
        return True


async def _run(dry_run: bool, study_id: uuid.UUID | None) -> None:
    ids = await _study_ids(study_id)
    repaired = sum([await _repair(item, dry_run) for item in ids])
    print(f"\nEstudios {'revisados' if dry_run else 'reparados'}: {repaired} de {len(ids)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="mostrar sin escribir")
    parser.add_argument("--study", type=uuid.UUID, default=None, help="un solo estudio")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run, args.study))


if __name__ == "__main__":
    main()
