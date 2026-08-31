from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.patient_report import PatientReport, PatientReportSource
from app.modules.patient_app.alert_actions import VEST_MISPLACED_KIND
from app.modules.patient_app.patient_app_repository import list_patient_alerts
from app.modules.patient_app.patient_app_schemas import MobileAlertStatus
from app.scripts.seed_patient_alerts import ALERTS, DEFAULT_DNI, seed_alerts


async def _alert_count(db: Any, patient_id: Any) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(Alert).where(Alert.patient_id == patient_id)
        )
        or 0
    )


async def test_seed_leaves_every_alert_pending(db: Any, make_patient: Callable[..., Any]) -> None:
    patient = await make_patient(dni=DEFAULT_DNI)

    await seed_alerts(db, DEFAULT_DNI)

    _, total, pending = await list_patient_alerts(
        db, patient.id, limit=50, offset=0, status=MobileAlertStatus.ALL
    )
    assert total == len(ALERTS)
    # El punto de la seed: todo lo que pide respuesta cuenta para el badge de
    # Inicio. El aviso de chaleco mal colocado no pide ninguna —se arregla
    # acomodándose el equipo, no contestando— así que queda fuera de la cuenta.
    answerable = [spec for spec in ALERTS if spec.kind != VEST_MISPLACED_KIND]
    assert pending == len(answerable)


async def test_seed_is_idempotent_and_additive(db: Any, make_patient: Callable[..., Any]) -> None:
    patient = await make_patient(dni=DEFAULT_DNI)
    other = await make_patient(dni="30999888")
    # Un aviso ajeno al script, para confirmar que la limpieza no barre de más.
    real = Alert(
        patient_id=patient.id,
        kind="tachycardia",
        severity=AlertSeverity.HIGH,
        message="Un aviso real que no escribió el script.",
    )
    db.add(real)
    await db.flush()

    await seed_alerts(db, DEFAULT_DNI)
    await seed_alerts(db, DEFAULT_DNI)

    assert await _alert_count(db, patient.id) == len(ALERTS) + 1
    assert await _alert_count(db, other.id) == 0
    assert await db.get(Alert, real.id) is not None


async def test_seed_clears_reports_answering_its_own_alerts(
    db: Any, make_patient: Callable[..., Any]
) -> None:
    """Reejecutar después de probar el formulario no puede chocar contra la FK."""
    patient = await make_patient(dni=DEFAULT_DNI)
    await seed_alerts(db, DEFAULT_DNI)

    answered = await db.scalar(select(Alert).where(Alert.patient_id == patient.id).limit(1))
    db.add(
        PatientReport(
            patient_id=patient.id,
            alert_id=answered.id,
            occurred_at=answered.created_at,
            source=PatientReportSource.PUSH_RESPONSE,
            symptoms=["mareo"],
            activity="caminando",
        )
    )
    await db.flush()

    await seed_alerts(db, DEFAULT_DNI)

    assert await _alert_count(db, patient.id) == len(ALERTS)
    assert (
        await db.scalar(
            select(func.count())
            .select_from(PatientReport)
            .where(PatientReport.patient_id == patient.id)
        )
        == 0
    )
