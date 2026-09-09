"""Reconstruye la línea de tiempo de pared de los estudios ya ingeridos.

`ecg_batch` viene guardando desde siempre `epoch_anchor_ms`, `device_uptime_ms`,
`boot_id`, `first_seq`, `last_seq` y `num_samples` de cada lote. Eso alcanza para
materializar los tramos de todo lo que ya está archivado: no hay que volver a
decodificar ni una trama.

La precisión es la vieja —el ancla se derivaba de nuestra hora de recepción, así
que arrastra la latencia del pedido— y por eso los tramos quedan marcados como
`server_receive`. Lo que sí queda bien son **los huecos**, que es lo que no se
podía ver: con el eje de muestras, un chaleco apagado cuatro horas no dejaba
ninguna marca.

Idempotente: borra los tramos del estudio antes de reescribirlos, así que se
puede correr las veces que haga falta.

    python -m app.scripts.backfill_timeline [--dry-run] [--study <uuid>]
"""

import argparse
import asyncio
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.ecg_batch import ECGBatch
from app.db.models.study import Study
from app.db.models.study_timeline_segment import StudyTimelineSegment, TimeSyncSource
from app.db.session import async_session_factory

#: Los lotes viejos no traen el `t0Ms` de sus tramas: se archivó el ancla y el
#: `batch_timestamp`, no la cabecera. La hora de inicio de cada lote se
#: reconstruye desde `batch_timestamp`, que es exactamente
#: `(epoch_anchor_ms + t0Ms_de_la_primera_trama) // 1000`.
SECOND_MS = 1000


async def _studies_to_backfill(db: AsyncSession, study_id: uuid.UUID | None) -> list[Study]:
    query = select(Study).where(Study.deleted_at.is_(None)).order_by(Study.started_at)
    if study_id is not None:
        query = query.where(Study.id == study_id)
    return list((await db.scalars(query)).all())


async def _batches(db: AsyncSession, study_id: uuid.UUID) -> list[ECGBatch]:
    return list(
        (
            await db.scalars(
                select(ECGBatch)
                .where(ECGBatch.study_id == study_id)
                .order_by(ECGBatch.first_seq.asc().nulls_last(), ECGBatch.created_at.asc())
            )
        ).all()
    )


def _segments_for(study: Study, batches: list[ECGBatch]) -> list[StudyTimelineSegment]:
    """Agrupa los lotes en tramos con las mismas reglas que la ingesta en vivo.

    Un tramo se corta cuando cambia el `bootId` o cuando el equipo dejó de grabar
    más que la tolerancia. El wraparound de `millis()` no se puede distinguir acá
    —haría falta el `t0Ms` crudo de las tramas, que estos lotes no guardaron—
    pero produce un salto enorme, así que cae igual en la segunda regla.

    El hueco se mide en **tiempo del equipo**, igual que en vivo. El `t0Ms` de
    cada lote se recupera de lo archivado: `batch_timestamp` es
    `(epoch_anchor_ms + t0Ms) // 1000`, así que restarle el ancla lo devuelve. Es
    lo que hace que la reconstrucción no herede la latencia del pedido — que en
    estos lotes es justamente lo peor que tienen, porque su ancla se derivó de
    nuestra hora de recepción.
    """
    segments: list[StudyTimelineSegment] = []
    sample_cursor = 0
    #: `batch_timestamp` está en segundos, así que cada `t0Ms` reconstruido
    #: arrastra hasta un segundo de truncamiento por punta. Se le da ese margen
    #: al hueco para no partir un tramo por el redondeo.
    tolerance_ms = settings.ingest_timeline_gap_tolerance_ms + 2 * SECOND_MS

    for batch in batches:
        samples = batch.num_samples or 0
        start_ms = (batch.batch_timestamp or 0) * SECOND_MS
        end_ms = start_ms + (batch.duration_seconds or 0) * SECOND_MS
        current = segments[-1] if segments else None
        anchor_ms = batch.epoch_anchor_ms
        first_t0_ms = start_ms - anchor_ms if anchor_ms is not None else None

        same_boot = current is not None and current.boot_id == batch.boot_id
        if current is not None and first_t0_ms is not None and current.last_t0_ms is not None:
            # `t0Ms` es milisegundos desde el arranque del equipo, así que los dos
            # lados de la resta son del mismo reloj aunque cada lote se haya
            # anclado con una hora de recepción distinta.
            contiguous = 0 <= first_t0_ms - current.last_t0_ms <= tolerance_ms
        else:
            drift_ms = abs(start_ms - current.end_epoch_ms) if current is not None else None
            contiguous = drift_ms is not None and drift_ms <= tolerance_ms
        if current is not None and same_boot and contiguous:
            current.sample_count += samples
            current.last_seq = batch.last_seq
            current.end_epoch_ms = max(current.end_epoch_ms, end_ms)
            current.last_t0_ms = current.end_epoch_ms - current.boot_epoch_ms
        else:
            boot_epoch_ms = batch.epoch_anchor_ms or start_ms
            segments.append(
                StudyTimelineSegment(
                    study_id=study.id,
                    ordinal=len(segments),
                    boot_id=batch.boot_id,
                    first_seq=batch.first_seq,
                    last_seq=batch.last_seq,
                    start_sample_index=sample_cursor,
                    sample_count=samples,
                    start_epoch_ms=start_ms,
                    end_epoch_ms=max(end_ms, start_ms),
                    # El `t0Ms` crudo de las tramas no se archivó, pero el ancla
                    # sí: la resta lo devuelve. Sin esto un tramo reconstruido no
                    # se puede continuar en vivo — `starts_new_segment` se queda
                    # sin la regla que mide en el reloj del equipo.
                    first_t0_ms=start_ms - boot_epoch_ms,
                    last_t0_ms=max(end_ms, start_ms) - boot_epoch_ms,
                    boot_epoch_ms=boot_epoch_ms,
                    anchor_slope_ppm=0,
                    # Estos lotes se anclaron con nuestra hora de recepción, y eso
                    # no se puede mejorar retroactivamente: queda declarado.
                    anchor_source=TimeSyncSource.SERVER_RECEIVE,
                    anchor_uncertainty_ms=10_000,
                )
            )
        sample_cursor += samples

    return segments


async def _run(dry_run: bool, study_id: uuid.UUID | None) -> None:
    async with async_session_factory() as db:
        studies = await _studies_to_backfill(db, study_id)
        rebuilt = skipped = 0

        for study in studies:
            batches = await _batches(db, study.id)
            if not batches:
                # Estudio seedeado o legacy: toda su señal está en un blob único
                # y no tiene huecos, así que el eje relativo ya es correcto.
                skipped += 1
                continue

            segments = _segments_for(study, batches)
            print(
                f"{study.id}  lotes={len(batches):>4}  tramos={len(segments):>3}"
                f"  muestras={sum(s.sample_count for s in segments):>9}"
                + ("  (dry-run)" if dry_run else "")
            )
            if dry_run:
                continue

            await db.execute(
                delete(StudyTimelineSegment).where(StudyTimelineSegment.study_id == study.id)
            )
            for segment in segments:
                db.add(segment)
            rebuilt += 1

        if not dry_run:
            await db.commit()
        print(f"\nEstudios reconstruidos: {rebuilt}. Sin lotes (seedeados/legacy): {skipped}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="mostrar sin escribir")
    parser.add_argument("--study", type=uuid.UUID, default=None, help="un solo estudio")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run, args.study))


if __name__ == "__main__":
    main()
