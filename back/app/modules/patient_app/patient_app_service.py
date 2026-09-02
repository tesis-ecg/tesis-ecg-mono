"""Servicio de la app móvil del paciente."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import Auth0Error, authenticate_user
from app.core.config import settings
from app.core.security import MOBILE_REFRESH, create_mobile_tokens
from app.db.models.audit_event import AuditEventType
from app.db.models.device import Device
from app.db.models.patient import Patient
from app.db.models.patient_report import PatientReport, PatientReportSource
from app.db.models.study import Study
from app.db.models.user import User, UserRole
from app.dependencies.patient_dependencies import resolve_patient_user
from app.modules._alert_kind import resolve_alert_kind
from app.modules.auth import auth_repository as auth_repo
from app.modules.auth.auth_service import enforce_rate_limits, rate_limit_key
from app.modules.patient_app import catalogs
from app.modules.patient_app import patient_app_repository as repo
from app.modules.patient_app.alert_actions import requires_patient_response
from app.modules.patient_app.patient_app_schemas import (
    CatalogOptionOut,
    MobileAccessOut,
    MobileAlertListInput,
    MobileAlertListResponse,
    MobileAlertOut,
    MobileCatalogsOut,
    MobileDeviceOut,
    MobileDoctorOut,
    MobileLoginInput,
    MobilePatientOut,
    MobileReportCreateInput,
    MobileReportCreateRequest,
    MobileReportGetInput,
    MobileReportListInput,
    MobileReportListResponse,
    MobileReportOut,
    MobileSessionOut,
    PushTokenInput,
)

#: Cuánto atrás puede fechar el paciente un registro. Un día cubre "me pasó
#: anoche" sin dejar que un reloj mal configurado ancle un síntoma a la semana
#: pasada, donde el médico no lo va a buscar nunca.
MAX_BACKDATE = timedelta(hours=24)
#: Margen hacia adelante para relojes adelantados. Nada más que eso: un registro
#: en el futuro no se puede correlacionar con ninguna señal.
MAX_FUTURE_SKEW = timedelta(minutes=5)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


def _invalid_credentials() -> HTTPException:
    """401 genérico, igual que en el portal.

    No distingue "ese DNI no existe" de "la contraseña está mal": la primera
    respuesta convertiría el login en un padrón de pacientes consultable.
    """
    return HTTPException(
        status_code=401,
        detail={"code": "INVALID_CREDENTIALS", "message": "Datos incorrectos."},
    )


async def _resolve_email(db: AsyncSession, identifier: str) -> str | None:
    """Traduce lo que escribió el paciente al email que entiende Auth0."""
    cleaned = identifier.strip()
    if "@" in cleaned:
        return cleaned.lower()
    user = await repo.get_user_by_dni(db, cleaned)
    return user.email if user is not None else None


async def _patient_out(db: AsyncSession, patient: Patient) -> MobilePatientOut:
    doctor_user = await repo.get_doctor_of_patient(db, patient.doctor_id)
    return MobilePatientOut(
        id=patient.id,
        fullName=f"{patient.first_name} {patient.last_name}".strip(),
        dni=patient.dni or "",
        birthDate=patient.date_of_birth,
        sex=patient.sex,
        email=patient.email,
        phone=patient.phone,
        studyStatus=patient.study_status,
        doctor=(
            MobileDoctorOut(fullName=doctor_user.full_name, email=doctor_user.email)
            if doctor_user is not None
            else None
        ),
    )


async def _session_out(db: AsyncSession, user: User, patient: Patient) -> MobileSessionOut:
    access, refresh, expires_at = create_mobile_tokens(user)
    return MobileSessionOut(
        accessToken=access,
        refreshToken=refresh,
        expiresAt=expires_at,
        patient=await _patient_out(db, patient),
    )


async def login(input_data: MobileLoginInput, db: AsyncSession) -> MobileSessionOut:
    identifier = input_data.data.identifier.strip()
    ip = input_data.ip or "unknown"
    await enforce_rate_limits(
        db,
        (
            (rate_limit_key("mobile-login-account", f"{identifier.lower()}|{ip}"), 5, 15 * 60),
            (rate_limit_key("mobile-login-ip", ip), 20, 15 * 60),
        ),
    )

    email = await _resolve_email(db, identifier)
    if email is None:
        # El DNI no existe. Se audita y se responde lo mismo que una contraseña
        # incorrecta, sin llamar a Auth0 — no hay a quién autenticar.
        await auth_repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            ip_address=input_data.ip,
            metadata={
                "identity_key": rate_limit_key("audit-dni", identifier),
                "error": "PATIENT_NOT_FOUND",
                "surface": "mobile",
            },
        )
        await db.commit()
        raise _invalid_credentials()

    try:
        auth0_id = await authenticate_user(email, input_data.data.password, input_data.ip)
    except Auth0Error as exc:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            ip_address=input_data.ip,
            metadata={
                "identity_key": rate_limit_key("audit-email", email),
                "error": exc.code,
                "surface": "mobile",
            },
        )
        await db.commit()
        # Cualquier fallo de credenciales se aplana al 401 genérico; los errores
        # de infraestructura de Auth0 (502) sí se propagan tal cual.
        if exc.status in (401, 403):
            raise _invalid_credentials() from exc
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc

    user = await auth_repo.get_user_by_auth0_id(db, auth0_id)
    if user is None or user.role != UserRole.PACIENTE:
        # Una cuenta de médico con credenciales válidas llega hasta acá. No entra:
        # el portal es su lugar, y `get_role_scope` tampoco la dejaría al revés.
        await auth_repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            user_id=user.id if user else None,
            ip_address=input_data.ip,
            metadata={"error": "NOT_A_PATIENT", "surface": "mobile"},
        )
        await db.commit()
        raise _invalid_credentials()
    if not user.is_active or user.identity_status.value != "active":
        await auth_repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            user_id=user.id,
            ip_address=input_data.ip,
            metadata={"error": "USER_INACTIVE", "surface": "mobile"},
        )
        await db.commit()
        raise HTTPException(
            status_code=403, detail={"code": "USER_INACTIVE", "message": "Cuenta inactiva."}
        )

    patient = await repo.get_patient_by_user_id(db, user.id)
    if patient is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "PATIENT_NOT_FOUND", "message": "La cuenta no tiene ficha activa."},
        )

    await auth_repo.log_audit_event(
        db,
        AuditEventType.LOGIN_OK,
        user_id=user.id,
        ip_address=input_data.ip,
        metadata={"surface": "mobile"},
    )
    await auth_repo.clear_rate_limit(
        db, rate_limit_key("mobile-login-account", f"{identifier.lower()}|{ip}")
    )
    session = await _session_out(db, user, patient)
    await db.commit()
    return session


async def refresh(refresh_token: str, db: AsyncSession) -> MobileAccessOut:
    user = await resolve_patient_user(db, refresh_token, MOBILE_REFRESH)
    access, _, expires_at = create_mobile_tokens(user)
    return MobileAccessOut(accessToken=access, expiresAt=expires_at)


async def logout(user: User, ip: str | None, db: AsyncSession) -> None:
    """Cierra la sesión y calla el celular.

    `set_last_logout` incrementa `session_version`, que mata al access y al
    refresh de una. Los push tokens se dan de baja en el mismo acto: si no, el
    paciente que cerró sesión seguiría recibiendo avisos suyos en un celular al
    que ya no puede entrar.
    """
    await auth_repo.set_last_logout(db, user.id)
    await repo.deactivate_push_tokens_for_user(db, user.id)
    await auth_repo.log_audit_event(
        db,
        AuditEventType.LOGOUT,
        user_id=user.id,
        ip_address=ip,
        metadata={"surface": "mobile"},
    )
    await db.commit()


async def get_me(patient: Patient, db: AsyncSession) -> MobilePatientOut:
    return await _patient_out(db, patient)


# --------------------------------------------------------------------------- #
# Dispositivo
# --------------------------------------------------------------------------- #


def _vest_placement(device: Device) -> str:
    """Lo último que el equipo reportó por `POST /ingest/device-status`.

    `None` es ``unknown`` y no ``ok``: un chaleco recién entregado no reportó
    nada todavía, y decirle al paciente que está bien puesto sin haberlo medido
    es exactamente el error que este aviso existe para evitar.
    """
    if device.placement_ok is None:
        return "unknown"
    return "ok" if device.placement_ok else "bad"


def _device_state(patient: Patient, device: Device, study: Study | None) -> str:
    if device.last_seen_at is None:
        return "never_connected"
    threshold = datetime.now(UTC) - timedelta(hours=settings.dashboard_stale_hours)
    last_data = patient.last_data_received_at
    if last_data is not None and last_data < threshold:
        return "stale"
    if study is None:
        return "idle"
    return "recording" if last_data is not None else "idle"


async def get_device(patient: Patient, db: AsyncSession) -> MobileDeviceOut:
    """Estado del chaleco. Nunca 404.

    A diferencia de `GET /devices/{id}/health`, que para el médico responde 404
    cuando el equipo nunca se conectó, acá "todavía no se encendió" es un estado
    legítimo que la pantalla del paciente tiene que poder dibujar.
    """
    device = await repo.get_assigned_device(db, patient.id)
    if device is None:
        return MobileDeviceOut(
            hasDevice=False,
            state="none",
            vestPlacement="unknown",
            vestPlacementAt=None,
            deviceId=None,
            serial=None,
            model=None,
            firmwareVersion=None,
            batteryPercent=None,
            lastSeenAt=None,
            lastDataReceivedAt=patient.last_data_received_at,
            studyId=None,
            studyStartedAt=None,
        )
    study = await repo.get_open_study(db, patient.id)
    return MobileDeviceOut(
        hasDevice=True,
        state=_device_state(patient, device, study),
        vestPlacement=_vest_placement(device),
        vestPlacementAt=device.placement_reported_at,
        deviceId=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version,
        batteryPercent=device.last_battery_pct,
        lastSeenAt=device.last_seen_at,
        lastDataReceivedAt=patient.last_data_received_at,
        studyId=study.id if study is not None else None,
        studyStartedAt=study.started_at if study is not None else None,
    )


# --------------------------------------------------------------------------- #
# Avisos
# --------------------------------------------------------------------------- #


async def list_alerts(
    input_data: MobileAlertListInput, db: AsyncSession
) -> MobileAlertListResponse:
    rows, total, pending_total = await repo.list_patient_alerts(
        db,
        input_data.patient_id,
        input_data.limit,
        input_data.offset,
        input_data.status,
    )
    items: list[MobileAlertOut] = []
    for alert, event_type, event_metadata, report_id, answered_at in rows:
        kind = resolve_alert_kind(alert.kind, event_type, event_metadata)
        requires_response = requires_patient_response(kind)
        items.append(
            MobileAlertOut(
                id=alert.id,
                kind=kind,
                severity=alert.severity.value.lower(),
                message=alert.message,
                detectedAt=alert.created_at,
                requiresResponse=requires_response,
                needsReport=requires_response and report_id is None,
                reportId=report_id,
                answeredAt=answered_at,
            )
        )
    return MobileAlertListResponse(
        items=items,
        total=total,
        pendingTotal=pending_total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


# --------------------------------------------------------------------------- #
# Bitácora
# --------------------------------------------------------------------------- #


def report_out(report: PatientReport) -> MobileReportOut:
    return MobileReportOut(
        id=report.id,
        occurredAt=report.occurred_at,
        source=report.source,
        symptoms=list(report.symptoms or []),
        symptomsOther=report.symptoms_other,
        activity=report.activity,
        activityOther=report.activity_other,
        notes=report.notes,
        alertId=report.alert_id,
        studyId=report.study_id,
        createdAt=report.created_at,
    )


async def list_reports(
    input_data: MobileReportListInput, db: AsyncSession
) -> MobileReportListResponse:
    rows, total = await repo.list_reports(
        db, input_data.patient_id, input_data.limit, input_data.offset
    )
    return MobileReportListResponse(
        items=[report_out(row) for row in rows],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


async def get_report(input_data: MobileReportGetInput, db: AsyncSession) -> MobileReportOut:
    report = await repo.get_patient_report(db, input_data.report_id, input_data.patient_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REPORT_NOT_FOUND", "message": "Registro no encontrado."},
        )
    return report_out(report)


def _clamp_occurred_at(value: datetime | None) -> datetime:
    now = datetime.now(UTC)
    if value is None:
        return now
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    # El reloj del celular puede estar corrido. Acotar en el servidor evita que
    # un registro quede anclado a un instante donde no hay ni va a haber señal.
    return min(max(moment, now - MAX_BACKDATE), now + MAX_FUTURE_SKEW)


def _validate_free_text(data: MobileReportCreateRequest) -> None:
    """`otro` sin texto es un registro que el médico no puede leer."""
    if catalogs.OTHER in data.symptoms and not (data.symptomsOther or "").strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SYMPTOM_DETAIL_REQUIRED",
                "message": "Contanos qué sentiste.",
                "fields": {"symptomsOther": "Requerido cuando elegís 'Otro'."},
            },
        )
    if data.activity == catalogs.OTHER and not (data.activityOther or "").strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ACTIVITY_DETAIL_REQUIRED",
                "message": "Contanos qué estabas haciendo.",
                "fields": {"activityOther": "Requerido cuando elegís 'Otra cosa'."},
            },
        )


async def create_report(input_data: MobileReportCreateInput, db: AsyncSession) -> MobileReportOut:
    """Crea (o actualiza) un registro de la bitácora.

    Dos detalles que hacen todo el trabajo:

    - `occurred_at` es hora de pared, no un offset en muestras. El chaleco sube
      cada ~1 h: el registro puede existir mucho antes que la señal de ese
      momento, y la conversión a coordenadas del gráfico se hace al leer el
      manifest.
    - Con `alertId`, la fila es idempotente por alerta. Un doble tap sobre la
      notificación actualiza el registro en vez de duplicar el dato clínico.
    """
    data = input_data.data
    _validate_free_text(data)
    occurred_at = _clamp_occurred_at(data.occurredAt)

    alert_id: uuid.UUID | None = None
    if data.alertId is not None:
        alert = await repo.get_patient_alert(db, data.alertId, input_data.patient_id)
        if alert is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "ALERT_NOT_FOUND", "message": "Aviso no encontrado."},
            )
        alert_id = alert.id
        # La hora del aviso manda sobre la del celular: es el instante que el
        # backend quiere correlacionar con la señal.
        if data.occurredAt is None:
            occurred_at = alert.created_at

    study = await repo.get_study_covering(db, input_data.patient_id, occurred_at)
    source = (
        PatientReportSource.PUSH_RESPONSE if alert_id is not None else PatientReportSource.MANUAL
    )

    report = await repo.get_report_by_alert(db, alert_id) if alert_id is not None else None
    if report is None:
        report = PatientReport(patient_id=input_data.patient_id, alert_id=alert_id, source=source)
        db.add(report)

    report.occurred_at = occurred_at
    report.study_id = study.id if study is not None else None
    report.symptoms = data.symptoms
    report.symptoms_other = (data.symptomsOther or "").strip() or None
    report.activity = data.activity
    report.activity_other = (data.activityOther or "").strip() or None
    report.notes = (data.notes or "").strip() or None
    await db.flush()

    await auth_repo.log_audit_event(
        db,
        AuditEventType.PATIENT_REPORT_CREATED,
        user_id=input_data.user.id,
        metadata={
            "target_patient_id": str(input_data.patient_id),
            "report_id": str(report.id),
            "study_id": str(report.study_id) if report.study_id else None,
            "source": source.value,
        },
    )
    await db.commit()
    return report_out(report)


def get_catalogs() -> MobileCatalogsOut:
    return MobileCatalogsOut(
        symptoms=[CatalogOptionOut(value=v, label=label) for v, label in catalogs.SYMPTOMS],
        activities=[CatalogOptionOut(value=v, label=label) for v, label in catalogs.ACTIVITIES],
    )


# --------------------------------------------------------------------------- #
# Push tokens
# --------------------------------------------------------------------------- #


async def register_push_token(input_data: PushTokenInput, db: AsyncSession) -> None:
    await repo.upsert_push_token(
        db, input_data.user.id, input_data.data.token.strip(), input_data.data.platform
    )
    await db.commit()


async def unregister_push_token(input_data: PushTokenInput, db: AsyncSession) -> None:
    await repo.delete_push_token(db, input_data.user.id, input_data.data.token.strip())
    await db.commit()


def schedule_alert_push(
    background_tasks: BackgroundTasks,
    patient_id: uuid.UUID,
    alert_id: uuid.UUID,
    occurred_at: datetime | None = None,
) -> None:
    """Helper para los llamadores que ya tienen un `BackgroundTasks` a mano.

    `occurred_at` es el instante del hallazgo, no el del envío. Viaja en el
    `data` del push y de ahí sale el `occurredAt` con el que la app abre el
    formulario: si dijera "ahora" para un hallazgo de hace veinte minutos, el
    registro del paciente quedaría anclado en el lugar equivocado de la traza.
    Sin dato, "ahora" es la mejor aproximación disponible.
    """
    from app.modules.patient_app.notifications_service import (
        anomaly_message,
        notify_patient_task,
    )

    background_tasks.add_task(
        notify_patient_task,
        patient_id,
        anomaly_message(alert_id, (occurred_at or datetime.now(UTC)).isoformat()),
    )
