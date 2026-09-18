import uuid
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import Alert
from app.db.models.device import Device
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment


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


async def get_recent_alert(
    db: AsyncSession, patient_id: uuid.UUID, kind: str, since: datetime
) -> Alert | None:
    """La última alerta de ese tipo dentro de la ventana. Es el debounce.

    Sin esto, un chaleco que rebota mientras el paciente se lo acomoda le manda
    una notificación por minuto — y el paciente termina silenciando la app justo
    para lo que la instaló.
    """
    result = await db.execute(
        select(Alert)
        .where(
            Alert.patient_id == patient_id,
            Alert.kind == kind,
            Alert.deleted_at.is_(None),
            Alert.created_at >= since,
        )
        .order_by(Alert.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


# --------------------------------------------------------------------------- #
# Línea de tiempo de pared
# --------------------------------------------------------------------------- #


async def get_last_timeline_segment(
    db: AsyncSession, study_id: uuid.UUID
) -> StudyTimelineSegment | None:
    """El tramo abierto más reciente del estudio, o `None` si todavía no hay.

    Se ordena por `ordinal` y no por hora: el ordinal es el orden en que se
    archivaron los tramos, que es lo que hay que continuar. La hora puede
    retroceder entre tramos si un ancla estaba mal, y ordenar por ella dejaría
    el cursor apuntando al tramo equivocado.
    """
    result = await db.execute(
        select(StudyTimelineSegment)
        .where(
            StudyTimelineSegment.study_id == study_id,
            StudyTimelineSegment.deleted_at.is_(None),
        )
        .order_by(desc(StudyTimelineSegment.ordinal))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_timeline_segments(
    db: AsyncSession, study_id: uuid.UUID
) -> list[StudyTimelineSegment]:
    """Todos los tramos del estudio, en orden de grabación."""
    result = await db.execute(
        select(StudyTimelineSegment)
        .where(
            StudyTimelineSegment.study_id == study_id,
            StudyTimelineSegment.deleted_at.is_(None),
        )
        .order_by(StudyTimelineSegment.ordinal)
    )
    return list(result.scalars().all())


async def add_timeline_segment(
    db: AsyncSession, segment: StudyTimelineSegment
) -> StudyTimelineSegment:
    db.add(segment)
    await db.flush()
    return segment


async def list_boot_anchors(
    db: AsyncSession, study_id: uuid.UUID, boot_id: int | None, since_seq: int | None
) -> list[ECGBatch]:
    """Lotes del mismo arranque que traen un ancla del puente, para el ajuste.

    Solo los que tienen `bridge_epoch_ms`: un ancla derivada de nuestra hora de
    recepción arrastra la latencia del pedido, y mezclarla con las buenas
    metería ese ruido dentro de la pendiente. Con menos de tres anclas buenas no
    se ajusta nada y se usa la última.
    """
    query = select(ECGBatch).where(
        ECGBatch.study_id == study_id,
        ECGBatch.boot_id == boot_id,
        ECGBatch.bridge_epoch_ms.is_not(None),
        # Un ancla es la pareja (uptime, epoch): sin el uptime no hay punto que
        # poner en la recta. Dejar entrar la fila y leer el uptime como 0 metería
        # un `(0, epoch)` entre anclas que están a horas de uptime, y un solo
        # punto así domina los mínimos cuadrados y clava la pendiente en la cota.
        ECGBatch.device_uptime_ms.is_not(None),
    )
    if since_seq is not None:
        query = query.where(ECGBatch.first_seq >= since_seq)
    result = await db.execute(query.order_by(ECGBatch.first_seq))
    return list(result.scalars().all())


async def has_archived_seq_range(
    db: AsyncSession, study_id: uuid.UUID, first_seq: int, last_seq: int
) -> bool:
    """¿Está archivado, sin huecos, todo un rango de `seq` del estudio?

    Es lo que distingue una retransmisión legítima de un `seq` que rebobinó.
    Cuando un lote llega entero por debajo del cursor bajo otro `bootId`, los dos
    casos son idénticos mirando solo los números (`INTEGRACION.md` §11.6) — la
    diferencia es que la retransmisión ya está archivada y el rebobinado no. Sin
    este chequeo, el rebobinado se confirmaba como duplicado y el equipo borraba
    de su flash señal que nunca llegó a existir de nuestro lado.
    """
    if last_seq < first_seq:
        return True

    result = await db.execute(
        select(ECGBatch.first_seq, ECGBatch.last_seq)
        .where(
            ECGBatch.study_id == study_id,
            ECGBatch.first_seq.is_not(None),
            ECGBatch.last_seq.is_not(None),
            ECGBatch.last_seq >= first_seq,
            ECGBatch.first_seq <= last_seq,
        )
        .order_by(ECGBatch.first_seq, ECGBatch.last_seq)
    )
    expected_seq = first_seq
    for batch_first, batch_last in result.all():
        if batch_first is None or batch_last is None:  # para el type checker
            continue
        if batch_first > expected_seq:
            return False
        expected_seq = max(expected_seq, batch_last + 1)
        if expected_seq > last_seq:
            return True
    return False
