"""Repositorio de la app móvil.

Todas las consultas están acotadas al paciente del token: acá no existe el
concepto de "scope de médico", porque un paciente solo se ve a sí mismo.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import Alert
from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.ecg_event import ECGEvent, ECGEventType
from app.db.models.patient import Patient
from app.db.models.patient_report import PatientReport
from app.db.models.push_token import PushPlatform, PushToken
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User, UserRole
from app.modules.patient_app.alert_actions import VEST_MISPLACED_KIND
from app.modules.patient_app.patient_app_schemas import MobileAlertStatus


async def get_patient_by_user_id(db: AsyncSession, user_id: uuid.UUID) -> Patient | None:
    result = await db.execute(
        select(Patient).where(Patient.user_id == user_id, Patient.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_user_by_dni(db: AsyncSession, dni: str) -> User | None:
    """La cuenta detrás de un DNI.

    Es el primer paso del login por DNI: Auth0 solo entiende email, así que hay
    que traducir antes de pedirle el ROPG. Se filtra por rol para que un DNI no
    pueda usarse nunca para llegar a una cuenta de médico o admin.
    """
    result = await db.execute(
        select(User)
        .join(Patient, Patient.user_id == User.id)
        .where(
            Patient.dni == dni,
            Patient.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.role == UserRole.PACIENTE,
        )
        .limit(1)
    )
    return result.scalars().first()


async def get_doctor_of_patient(db: AsyncSession, doctor_id: uuid.UUID) -> User | None:
    """El usuario del médico responsable, para mostrar su nombre en la app."""
    result = await db.execute(
        select(User)
        .join(Doctor, Doctor.user_id == User.id)
        .where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None), User.deleted_at.is_(None))
        .limit(1)
    )
    return result.scalars().first()


async def get_assigned_device(db: AsyncSession, patient_id: uuid.UUID) -> Device | None:
    # `first()` y no `scalar_one_or_none()`: con dos equipos asignados al mismo
    # paciente (carrera entre dos assign) el segundo sería un 500.
    result = await db.execute(
        select(Device)
        .where(
            Device.patient_id == patient_id,
            Device.status == DeviceStatus.ASSIGNED,
            Device.deleted_at.is_(None),
        )
        .order_by(Device.created_at.desc(), Device.id.asc())
        .limit(1)
    )
    return result.scalars().first()


async def get_open_study(db: AsyncSession, patient_id: uuid.UUID) -> Study | None:
    result = await db.execute(
        select(Study)
        .where(
            Study.patient_id == patient_id,
            Study.status == StudyStatus.IN_PROGRESS,
            Study.deleted_at.is_(None),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_study_covering(
    db: AsyncSession, patient_id: uuid.UUID, moment: datetime
) -> Study | None:
    """El estudio del paciente que estaba corriendo en ese instante.

    No exige que la señal de ese momento ya haya llegado — justamente el caso
    que motiva toda la tabla `patient_report`. Solo pregunta si el instante cae
    dentro de la ventana administrativa del estudio.
    """
    result = await db.execute(
        select(Study)
        .where(
            Study.patient_id == patient_id,
            Study.deleted_at.is_(None),
            Study.started_at <= moment,
            or_(Study.ended_at.is_(None), Study.ended_at >= moment),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
    )
    return result.scalars().first()


type AlertRow = tuple[
    Alert,
    ECGEventType | None,
    dict[str, Any] | None,
    uuid.UUID | None,
    datetime | None,
]


async def list_patient_alerts(
    db: AsyncSession,
    patient_id: uuid.UUID,
    limit: int,
    offset: int,
    status: MobileAlertStatus,
) -> tuple[list[AlertRow], int, int]:
    """Avisos paginados, total filtrado y total global pendiente."""
    live_event = or_(ECGEvent.id.is_(None), ECGEvent.deleted_at.is_(None))
    live_report = and_(
        PatientReport.alert_id == Alert.id,
        PatientReport.deleted_at.is_(None),
    )
    requires_response = or_(
        Alert.kind.is_(None),
        Alert.kind != VEST_MISPLACED_KIND,
    )
    # El filtro se arma una vez y se aplica a la página y al total: si los dos
    # no filtran igual, la lista dice "5 avisos" y dibuja 2.
    status_filter = {
        MobileAlertStatus.PENDING: (requires_response, PatientReport.id.is_(None)),
        MobileAlertStatus.ANSWERED: (requires_response, PatientReport.id.is_not(None)),
    }.get(status, ())
    base = (
        select(
            Alert,
            ECGEvent.event_type,
            ECGEvent.event_metadata,
            PatientReport.id,
            PatientReport.created_at,
        )
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .outerjoin(PatientReport, live_report)
        .where(Alert.patient_id == patient_id, Alert.deleted_at.is_(None), live_event)
    )
    if status_filter:
        base = base.where(*status_filter)
    result = await db.execute(
        base.order_by(Alert.created_at.desc(), Alert.id.asc()).limit(limit).offset(offset)
    )
    rows: list[AlertRow] = [
        (alert, event_type, event_metadata, report_id, answered_at)
        for alert, event_type, event_metadata, report_id, answered_at in result.all()
    ]

    total_query = (
        select(func.count())
        .select_from(Alert)
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .outerjoin(PatientReport, live_report)
        .where(Alert.patient_id == patient_id, Alert.deleted_at.is_(None), live_event)
    )
    if status_filter:
        total_query = total_query.where(*status_filter)
    total = await db.scalar(total_query)

    pending = await db.scalar(
        select(func.count())
        .select_from(Alert)
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .outerjoin(PatientReport, live_report)
        .where(
            Alert.patient_id == patient_id,
            Alert.deleted_at.is_(None),
            live_event,
            requires_response,
            PatientReport.id.is_(None),
        )
    )
    return rows, total or 0, pending or 0


async def get_patient_alert(
    db: AsyncSession, alert_id: uuid.UUID, patient_id: uuid.UUID
) -> Alert | None:
    result = await db.execute(
        select(Alert).where(
            Alert.id == alert_id,
            Alert.patient_id == patient_id,
            Alert.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_reports(
    db: AsyncSession, patient_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[PatientReport], int]:
    base = select(PatientReport).where(
        PatientReport.patient_id == patient_id, PatientReport.deleted_at.is_(None)
    )
    result = await db.scalars(
        base.order_by(PatientReport.occurred_at.desc(), PatientReport.id.asc())
        .limit(limit)
        .offset(offset)
    )
    total = await db.scalar(
        select(func.count())
        .select_from(PatientReport)
        .where(PatientReport.patient_id == patient_id, PatientReport.deleted_at.is_(None))
    )
    return list(result.all()), total or 0


async def get_patient_report(
    db: AsyncSession, report_id: uuid.UUID, patient_id: uuid.UUID
) -> PatientReport | None:
    result = await db.execute(
        select(PatientReport).where(
            PatientReport.id == report_id,
            PatientReport.patient_id == patient_id,
            PatientReport.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_reports_for_study(db: AsyncSession, study_id: uuid.UUID) -> list[PatientReport]:
    result = await db.scalars(
        select(PatientReport)
        .where(PatientReport.study_id == study_id, PatientReport.deleted_at.is_(None))
        .order_by(PatientReport.occurred_at.asc(), PatientReport.id.asc())
    )
    return list(result.all())


async def get_report_by_alert(db: AsyncSession, alert_id: uuid.UUID) -> PatientReport | None:
    result = await db.execute(
        select(PatientReport)
        .where(PatientReport.alert_id == alert_id, PatientReport.deleted_at.is_(None))
        .with_for_update()
    )
    return result.scalars().first()


# --------------------------------------------------------------------------- #
# Push tokens
# --------------------------------------------------------------------------- #


async def upsert_push_token(
    db: AsyncSession, user_id: uuid.UUID, token: str, platform: PushPlatform
) -> PushToken:
    """Registra el token, reclamándolo si estaba a nombre de otra cuenta.

    Pasa de verdad: el paciente presta el celular, o dos personas usan el mismo
    equipo. El índice único es sobre el token, no sobre `(user, token)`, así que
    la fila se reasigna en vez de duplicarse — si no, el aviso de uno le llegaría
    al otro.
    """
    now = datetime.now(UTC)
    existing = (
        await db.execute(
            select(PushToken).where(PushToken.token == token, PushToken.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.user_id = user_id
        existing.platform = platform
        existing.last_used_at = now
        await db.flush()
        return existing
    row = PushToken(user_id=user_id, token=token, platform=platform, last_used_at=now)
    db.add(row)
    await db.flush()
    return row


async def delete_push_token(db: AsyncSession, user_id: uuid.UUID, token: str) -> None:
    await db.execute(
        update(PushToken)
        .where(
            PushToken.user_id == user_id,
            PushToken.token == token,
            PushToken.deleted_at.is_(None),
        )
        .values(deleted_at=datetime.now(UTC))
    )


async def list_push_tokens(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    result = await db.scalars(
        select(PushToken.token).where(PushToken.user_id == user_id, PushToken.deleted_at.is_(None))
    )
    return list(result.all())


async def list_push_tokens_for_patient(db: AsyncSession, patient_id: uuid.UUID) -> list[str]:
    result = await db.scalars(
        select(PushToken.token)
        .join(Patient, Patient.user_id == PushToken.user_id)
        .where(Patient.id == patient_id, PushToken.deleted_at.is_(None))
    )
    return list(result.all())


async def deactivate_push_tokens(db: AsyncSession, tokens: list[str]) -> None:
    """Baja de los tokens que Expo reportó como muertos.

    Sin esto la lista se pudre: cada envío arrastra los tokens de celulares que
    ya desinstalaron la app y el resultado es cada vez más ruido en el log.
    """
    if not tokens:
        return
    await db.execute(
        update(PushToken)
        .where(PushToken.token.in_(tokens), PushToken.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC))
    )


async def deactivate_push_tokens_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(PushToken)
        .where(PushToken.user_id == user_id, PushToken.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC))
    )
