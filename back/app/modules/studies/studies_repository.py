import uuid

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device
from app.db.models.doctor import Doctor
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User


def _apply_study_filters(
    statement: Select[tuple[Study, Patient, Device, str | None]] | Select[tuple[int]],
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[StudyStatus] | None,
) -> Select[tuple[Study, Patient, Device, str | None]] | Select[tuple[int]]:
    statement = statement.where(
        Study.deleted_at.is_(None),
        Patient.deleted_at.is_(None),
        Device.deleted_at.is_(None),
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    if q:
        pattern = f"%{q.strip()}%"
        full_name = func.concat(Patient.first_name, " ", Patient.last_name)
        statement = statement.where(
            or_(
                full_name.ilike(pattern),
                Device.serial_number.ilike(pattern),
                cast(Study.id, String).ilike(pattern),
            )
        )
    if statuses:
        statement = statement.where(Study.status.in_(statuses))
    return statement


async def list_studies(
    db: AsyncSession,
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[StudyStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Study, Patient, Device, str | None]], int]:
    doctor_name = (
        select(User.full_name)
        .join(Doctor, Doctor.user_id == User.id)
        .where(
            Doctor.id == Patient.doctor_id,
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .limit(1)
        .scalar_subquery()
    )
    statement = _apply_study_filters(
        select(Study, Patient, Device, doctor_name.label("doctor_name"))
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id),
        doctor_id,
        q,
        statuses,
        # `Study.id` desempata para que la paginación tenga un orden total.
    ).order_by(Study.started_at.desc(), Study.id.asc())
    count_statement = _apply_study_filters(
        select(func.count())
        .select_from(Study)
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id),
        doctor_id,
        q,
        statuses,
    )

    result = await db.execute(statement.limit(limit).offset(offset))
    total = await db.scalar(count_statement)
    rows = [(study, patient, device, name) for study, patient, device, name in result.all()]
    return rows, total or 0


async def list_for_patient(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> tuple[list[Study], int] | None:
    patient_statement = (
        select(func.count())
        .select_from(Patient)
        .where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if doctor_id is not None:
        patient_statement = patient_statement.where(Patient.doctor_id == doctor_id)
    patient_exists = await db.scalar(patient_statement)
    if not patient_exists:
        return None

    statement = (
        select(Study)
        .where(Study.patient_id == patient_id, Study.deleted_at.is_(None))
        .order_by(Study.started_at.desc())
    )
    count_statement = (
        select(func.count())
        .select_from(Study)
        .where(Study.patient_id == patient_id, Study.deleted_at.is_(None))
    )
    result = await db.execute(statement)
    total = await db.scalar(count_statement)
    return list(result.scalars().all()), total or 0


async def get_detail(
    db: AsyncSession, study_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> tuple[Study, Patient, Device, str | None] | None:
    doctor_name = (
        select(User.full_name)
        .join(Doctor, Doctor.user_id == User.id)
        .where(
            Doctor.id == Patient.doctor_id,
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        select(Study, Patient, Device, doctor_name.label("doctor_name"))
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.id == study_id,
            Study.deleted_at.is_(None),
            Patient.deleted_at.is_(None),
            Device.deleted_at.is_(None),
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement)
    row = result.one_or_none()
    if row is None:
        return None
    study, patient, device, name = row
    return study, patient, device, name


async def list_ecg_events(db: AsyncSession, study_id: uuid.UUID) -> list[ECGEvent]:
    """Eventos del estudio, incluyendo seeds legacy sin ``ecg_batch.study_id``.

    Todo lote producido por la ingesta moderna tiene FK al estudio. Los datos
    demo previos a esa columna guardaban el vínculo únicamente en JSONB; el
    fallback conserva su visualización hasta que se regeneren.
    """
    metadata_study_id = ECGEvent.event_metadata["studyId"].astext
    result = await db.scalars(
        select(ECGEvent)
        .join(ECGBatch, ECGEvent.batch_id == ECGBatch.id)
        .where(
            ECGEvent.deleted_at.is_(None),
            ECGBatch.deleted_at.is_(None),
            or_(ECGBatch.study_id == study_id, metadata_study_id == str(study_id)),
        )
        .order_by(ECGEvent.timestamp_in_recording.asc(), ECGEvent.id.asc())
    )
    return list(result.all())


#: Estados desde los que un estudio todavía puede cerrarse. El resto
#: (`COMPLETED`, `CANCELLED`) son terminales.
OPEN_STATUSES = (StudyStatus.IN_PROGRESS, StudyStatus.SCHEDULED)


async def get_for_update(
    db: AsyncSession, study_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> tuple[Study, Patient] | None:
    """El estudio y su paciente, con la fila del estudio bloqueada.

    El `FOR UPDATE` serializa el cierre contra la ingesta, que bloquea esa misma
    fila para avanzar el cursor de ACK. Sin esto, un lote en vuelo podría
    escribir sobre un estudio que el médico acaba de cerrar.

    `of=Study` y no un bloqueo de toda la consulta: bloquear también `patient`
    metería a este flujo en carreras con la edición del paciente, que no tienen
    nada que ver.
    """
    statement = (
        select(Study, Patient)
        .join(Patient, Study.patient_id == Patient.id)
        .where(
            Study.id == study_id,
            Study.deleted_at.is_(None),
            Patient.deleted_at.is_(None),
        )
        .with_for_update(of=Study)
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        return None
    study, patient = row
    return study, patient


async def list_open_for_device(
    db: AsyncSession, patient_id: uuid.UUID, device_id: uuid.UUID
) -> list[Study]:
    """Estudios abiertos del par paciente+equipo, bloqueados.

    Devuelve una lista y no un único estudio a propósito: los datos previos a
    que existiera el cierre automático pueden tener más de uno abierto para el
    mismo par, y dejar uno sin cerrar reproduce el bug que esto viene a arreglar.

    A diferencia de `ingest_repository.get_open_study_for_update`, acá **no** se
    filtra por `ecg_s3_key IS NULL`: para cerrar da lo mismo si la señal vive en
    un blob o en segmentos.
    """
    result = await db.execute(
        select(Study)
        .where(
            Study.patient_id == patient_id,
            Study.device_id == device_id,
            Study.status.in_(OPEN_STATUSES),
            Study.deleted_at.is_(None),
        )
        .order_by(Study.started_at.desc())
        .with_for_update()
    )
    return list(result.scalars().all())


async def list_open_for_patient(db: AsyncSession, patient_id: uuid.UUID) -> list[Study]:
    """Todos los estudios abiertos del paciente, con cualquier equipo."""
    result = await db.execute(
        select(Study)
        .where(
            Study.patient_id == patient_id,
            Study.status.in_(OPEN_STATUSES),
            Study.deleted_at.is_(None),
        )
        .order_by(Study.started_at.desc())
        .with_for_update()
    )
    return list(result.scalars().all())


async def has_open_study(db: AsyncSession, patient_id: uuid.UUID) -> bool:
    """¿Al paciente le queda algún estudio abierto, con cualquier equipo?

    Se consulta después de cerrar uno para decidir el `study_status` del
    paciente. El autoflush de SQLAlchemy garantiza que el cierre pendiente en la
    sesión ya esté escrito cuando corre este SELECT.
    """
    found = await db.scalar(
        select(Study.id)
        .where(
            Study.patient_id == patient_id,
            Study.status.in_(OPEN_STATUSES),
            Study.deleted_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


async def get_patient_for_update(db: AsyncSession, patient_id: uuid.UUID) -> Patient | None:
    result = await db.execute(
        select(Patient)
        .where(Patient.id == patient_id, Patient.deleted_at.is_(None))
        .with_for_update()
    )
    return result.scalar_one_or_none()
