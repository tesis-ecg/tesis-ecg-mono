import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus


async def get_device_by_serial(db: AsyncSession, serial: str) -> Device | None:
    result = await db.execute(
        select(Device).where(Device.serial_number == serial, Device.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_device_for_update(db: AsyncSession, device_id: uuid.UUID) -> Device | None:
    """Recarga y bloquea el equipo para serializar ingesta y reasignaciones."""
    result = await db.execute(
        select(Device)
        .where(Device.id == device_id, Device.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_active_patient(db: AsyncSession, patient_id: uuid.UUID) -> Patient | None:
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_open_study_for_update(
    db: AsyncSession, patient_id: uuid.UUID, device_id: uuid.UUID
) -> Study | None:
    """Estudio en curso del par paciente+equipo, bloqueado para escritura.

    El `FOR UPDATE` es lo que hace que dos batches concurrentes del mismo equipo
    no se pisen al avanzar `last_ingested_seq` ni al reconstruir la pirámide: el
    segundo espera al primero en vez de leer un cursor viejo.

    El filtro por `device_id` es deliberado. Si el equipo fue reasignado a otro
    paciente, el estudio abierto del paciente anterior **no** puede recibir esta
    señal — sería archivar el registro de una persona bajo otra.

    El filtro por `ecg_s3_key IS NULL` descarta los estudios cuya señal vive en
    un blob único (los que escribe `seed_demo`, y cualquier estudio legacy). Las
    dos representaciones no se pueden fusionar: la pirámide de un estudio
    ingestado se reconstruye desde sus segmentos, así que agregarle lotes a uno
    que ya tiene blob dejaría `samples_count` contando muestras que la pirámide
    no cubre. Un lote sobre un estudio así abre uno nuevo.
    """
    result = await db.execute(
        select(Study)
        .where(
            Study.patient_id == patient_id,
            Study.device_id == device_id,
            Study.status == StudyStatus.IN_PROGRESS,
            Study.deleted_at.is_(None),
            Study.ecg_s3_key.is_(None),
        )
        .order_by(Study.started_at.desc())
        .with_for_update()
    )
    return result.scalars().first()


async def create_study(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    device_id: uuid.UUID,
    started_at: datetime,
    sample_rate: int,
) -> Study:
    study = Study(
        patient_id=patient_id,
        device_id=device_id,
        started_at=started_at,
        status=StudyStatus.IN_PROGRESS,
        sample_rate=sample_rate,
        ecg_encoding="float32-le",
    )
    db.add(study)
    await db.flush()
    return study


async def create_batch(db: AsyncSession, batch: ECGBatch) -> ECGBatch:
    db.add(batch)
    await db.flush()
    return batch


async def get_batch(db: AsyncSession, batch_id: uuid.UUID) -> ECGBatch | None:
    result = await db.execute(select(ECGBatch).where(ECGBatch.id == batch_id))
    return result.scalar_one_or_none()


async def get_study_for_update(db: AsyncSession, study_id: uuid.UUID) -> Study | None:
    result = await db.execute(select(Study).where(Study.id == study_id).with_for_update())
    return result.scalar_one_or_none()


async def list_batches_to_process(db: AsyncSession, study_id: uuid.UUID) -> list[ECGBatch]:
    """Lotes no terminados del estudio, en el único orden válido de señal."""
    result = await db.execute(
        select(ECGBatch)
        .where(
            ECGBatch.study_id == study_id,
            ECGBatch.processing_status != ProcessingStatus.DONE,
        )
        .order_by(ECGBatch.first_seq.asc().nulls_last(), ECGBatch.created_at.asc())
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())
