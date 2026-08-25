"""Alerts repository."""

import uuid
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ScalarSelect

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.doctor import Doctor
from app.db.models.ecg_event import ECGEvent, ECGEventType
from app.db.models.patient import Patient
from app.db.models.study import Study
from app.db.models.user import User

#: Mismo orden que usa el widget del dashboard: primero lo que puede matar a
#: alguien. Duplicarlo acá evita importar un privado de otro módulo.
_SEVERITY_RANK = case(
    {
        AlertSeverity.CRITICAL: 0,
        AlertSeverity.HIGH: 1,
        AlertSeverity.MEDIUM: 2,
        AlertSeverity.LOW: 3,
    },
    value=Alert.severity,
    else_=4,
)


def _study_id_subquery() -> ScalarSelect[uuid.UUID]:
    """El estudio que estaba corriendo cuando se detectó la alerta.

    Misma correlación que `dashboard_repository.list_pending_alerts`: la alerta
    no guarda un FK al estudio, así que se lo ubica por ventana temporal.
    """
    return (
        select(Study.id)
        .where(
            Study.patient_id == Alert.patient_id,
            Study.deleted_at.is_(None),
            Study.started_at <= Alert.created_at,
            (Study.ended_at.is_(None)) | (Study.ended_at >= Alert.created_at),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
        .scalar_subquery()
    )


def _acknowledged_by_subquery() -> ScalarSelect[str]:
    return (
        select(User.full_name)
        .join(Doctor, Doctor.user_id == User.id)
        .where(Doctor.id == Alert.acknowledged_by, Doctor.deleted_at.is_(None))
        .limit(1)
        .scalar_subquery()
    )


def _scoped[S: Select[Any]](statement: S, doctor_id: uuid.UUID | None) -> S:
    """Filas vivas y visibles para el scope. Es la base de todas las consultas."""
    statement = statement.where(
        Alert.deleted_at.is_(None),
        Patient.deleted_at.is_(None),
        ECGEvent.deleted_at.is_(None),
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    return statement


def _filtered[S: Select[Any]](
    statement: S,
    doctor_id: uuid.UUID | None,
    acknowledged: bool | None,
    severities: list[AlertSeverity] | None,
) -> S:
    statement = _scoped(statement, doctor_id)
    if acknowledged is True:
        statement = statement.where(Alert.acknowledged_at.is_not(None))
    elif acknowledged is False:
        statement = statement.where(Alert.acknowledged_at.is_(None))
    if severities:
        statement = statement.where(Alert.severity.in_(severities))
    return statement


async def list_alerts(
    db: AsyncSession,
    doctor_id: uuid.UUID | None,
    acknowledged: bool | None,
    severities: list[AlertSeverity] | None,
    limit: int,
    offset: int,
) -> tuple[
    list[tuple[Alert, Patient, ECGEventType, dict[str, Any], uuid.UUID | None, str | None]],
    int,
    int,
]:
    """Alertas paginadas + total filtrado + total de pendientes.

    El tercer valor no depende de los filtros a propósito: es el número del
    badge del menú, que tiene que seguir mostrando cuántas quedan aunque el
    médico esté mirando las ya acusadas.
    """
    joined = (
        select(
            Alert,
            Patient,
            ECGEvent.event_type,
            ECGEvent.event_metadata,
            _study_id_subquery().label("study_id"),
            _acknowledged_by_subquery().label("acknowledged_by_name"),
        )
        .join(Patient, Alert.patient_id == Patient.id)
        .join(ECGEvent, Alert.event_id == ECGEvent.id)
    )
    counted = (
        select(func.count())
        .select_from(Alert)
        .join(Patient, Alert.patient_id == Patient.id)
        .join(ECGEvent, Alert.event_id == ECGEvent.id)
    )

    # `Alert.id` desempata: sin orden total, dos alertas del mismo instante y
    # severidad pueden repetirse entre páginas.
    statement = _filtered(joined, doctor_id, acknowledged, severities).order_by(
        _SEVERITY_RANK.asc(), Alert.created_at.desc(), Alert.id.asc()
    )
    result = await db.execute(statement.limit(limit).offset(offset))
    total = await db.scalar(_filtered(counted, doctor_id, acknowledged, severities))
    pending_total = await db.scalar(
        _scoped(counted, doctor_id).where(Alert.acknowledged_at.is_(None))
    )

    rows = [
        (alert, patient, event_type, event_metadata, study_id, acknowledged_by_name)
        for (
            alert,
            patient,
            event_type,
            event_metadata,
            study_id,
            acknowledged_by_name,
        ) in result.all()
    ]
    return rows, total or 0, pending_total or 0


async def get_detail(
    db: AsyncSession, alert_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> tuple[Alert, Patient, ECGEventType, dict[str, Any], uuid.UUID | None, str | None] | None:
    """La misma fila enriquecida que devuelve el listado, para una sola alerta."""
    statement = (
        select(
            Alert,
            Patient,
            ECGEvent.event_type,
            ECGEvent.event_metadata,
            _study_id_subquery().label("study_id"),
            _acknowledged_by_subquery().label("acknowledged_by_name"),
        )
        .join(Patient, Alert.patient_id == Patient.id)
        .join(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.id == alert_id,
            Alert.deleted_at.is_(None),
            Patient.deleted_at.is_(None),
            ECGEvent.deleted_at.is_(None),
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        return None
    alert, patient, event_type, event_metadata, study_id, acknowledged_by_name = row
    return alert, patient, event_type, event_metadata, study_id, acknowledged_by_name


async def get_for_update(
    db: AsyncSession, alert_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> Alert | None:
    statement = (
        select(Alert)
        .join(Patient, Alert.patient_id == Patient.id)
        .where(
            Alert.id == alert_id,
            Alert.deleted_at.is_(None),
            Patient.deleted_at.is_(None),
        )
        .with_for_update(of=Alert)
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    return (await db.execute(statement)).scalar_one_or_none()
