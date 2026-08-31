"""Patients service."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import (
    Auth0Error,
    block_auth0_user,
    create_auth0_user,
    trigger_password_reset,
    update_auth0_user_email,
    update_auth0_user_password,
)
from app.core.passwords import generate_patient_password
from app.db.models.audit_event import AuditEventType
from app.db.models.patient import Patient
from app.db.models.user import IdentityStatus, User, UserRole
from app.modules.auth import auth_repository as auth_repo
from app.modules.devices.devices_schemas import HolterHealthOut
from app.modules.devices.devices_service import holter_health_out
from app.modules.doctors import doctors_repository as doctors_repo
from app.modules.patient_app import patient_app_repository as patient_app_repo
from app.modules.patients import patients_repository as repo
from app.modules.patients.patients_schemas import (
    PatientCreateInput,
    PatientCreateOut,
    PatientIdInput,
    PatientListInput,
    PatientListResponse,
    PatientOut,
    PatientPasswordOut,
    PatientRow,
    PatientSummaryInput,
    PatientSummaryOut,
    PatientUpdateInput,
)
from app.modules.studies import studies_service as studies
from app.modules.users import users_repository as users_repo


def _split_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NAME", "message": "Nombre inválido."},
        )
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _patient_out(row: PatientRow) -> PatientOut:
    if row.date_of_birth is None:
        raise HTTPException(
            status_code=500,
            detail={"code": "INVALID_PATIENT", "message": "Paciente sin fecha de nacimiento."},
        )
    return PatientOut(
        id=row.id,
        fullName=f"{row.first_name} {row.last_name}".strip(),
        dni=row.dni or "",
        birthDate=row.date_of_birth,
        sex=row.sex,
        assignedDeviceId=row.assigned_device_id,
        assignedDeviceSerial=row.assigned_device_serial,
        studyStatus=row.study_status,
        lastDataReceivedAt=row.last_data_received_at,
        contactEmail=row.email,
        contactPhone=row.phone,
        doctorId=row.doctor_id,
        doctorName=row.doctor_name,
        hasAppAccount=row.user_id is not None,
    )


async def list_patients(input_data: PatientListInput, db: AsyncSession) -> PatientListResponse:
    rows, total = await repo.list_patients(
        db,
        doctor_id=input_data.doctor_id,
        q=input_data.q,
        statuses=input_data.status,
        limit=input_data.limit,
        offset=input_data.offset,
        sort=input_data.sort,
        order=input_data.order,
        has_device=input_data.has_device,
    )
    return PatientListResponse(
        items=[_patient_out(row) for row in rows],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


async def get_patient(input_data: PatientIdInput, db: AsyncSession) -> PatientOut:
    row = await repo.get_patient_row(db, input_data.patient_id, input_data.doctor_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    return _patient_out(row)


def _email_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "EMAIL_CONFLICT",
            "message": "Ese email ya está en uso por otra cuenta.",
        },
    )


async def _provision_app_account(
    db: AsyncSession, patient: Patient, email: str, full_name: str
) -> str:
    """Crea la cuenta de la app del paciente y devuelve su contraseña inicial.

    Sigue el mismo protocolo de compensación que `users_service.create_user`:
    primero la fila local en estado `PENDING`, después Auth0, y recién entonces
    `ACTIVE`. Si Auth0 falla, el paciente queda creado con la cuenta en `ERROR`
    y el portal puede reintentar — nunca al revés, porque un usuario en Auth0
    sin fila local es un huérfano que nadie ve.
    """
    password = generate_patient_password()
    user = await users_repo.create_user(
        db,
        auth0_id=None,
        email=email,
        full_name=full_name,
        role=UserRole.PACIENTE,
        is_active=False,
        identity_status=IdentityStatus.PENDING,
    )
    patient.user_id = user.id
    await db.flush()

    try:
        auth0_id = await create_auth0_user(email, password, full_name)
    except Auth0Error as exc:
        user.identity_status = IdentityStatus.ERROR
        await db.commit()
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc

    user.auth0_id = auth0_id
    user.is_active = True
    user.identity_status = IdentityStatus.ACTIVE
    await db.flush()
    return password


async def create_patient(input_data: PatientCreateInput, db: AsyncSession) -> PatientCreateOut:
    if input_data.requesting_user.role == UserRole.ADMIN:
        if input_data.data.doctorId is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "DOCTOR_REQUIRED",
                    "message": "Seleccioná el médico responsable del paciente.",
                },
            )
        doctor = await doctors_repo.get_active_by_id(db, input_data.data.doctorId)
        if doctor is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "DOCTOR_NOT_FOUND", "message": "Médico no encontrado."},
            )
        doctor_id = doctor.id
    else:
        if input_data.data.doctorId is not None:
            raise HTTPException(
                status_code=422,
                detail={"code": "DOCTOR_FORBIDDEN", "message": "No podés elegir otro médico."},
            )
        if input_data.doctor_id is None:
            raise HTTPException(
                status_code=403,
                detail={"code": "FORBIDDEN", "message": "Sin perfil médico."},
            )
        doctor_id = input_data.doctor_id

    existing = await repo.get_patient_by_dni(db, input_data.data.dni)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DNI_CONFLICT", "message": "Ya existe un paciente con ese DNI."},
        )

    email = str(input_data.data.contactEmail).lower()
    # El chequeo va sin filtro de soft-delete: `user.email` es UNIQUE a secas, y
    # una cuenta dada de baja sigue ocupando el email.
    if await users_repo.get_user_by_email_any(db, email) is not None:
        raise _email_conflict()

    first_name, last_name = _split_full_name(input_data.data.fullName)
    full_name = f"{first_name} {last_name}".strip()
    try:
        patient = await repo.create_patient(
            db,
            doctor_id=doctor_id,
            first_name=first_name,
            last_name=last_name,
            dni=input_data.data.dni.strip(),
            date_of_birth=input_data.data.birthDate,
            sex=input_data.data.sex,
            email=email,
            phone=input_data.data.contactPhone.strip() if input_data.data.contactPhone else None,
        )
        await auth_repo.log_audit_event(
            db,
            AuditEventType.PATIENT_CREATED,
            user_id=input_data.requesting_user.id,
            metadata={"target_patient_id": str(patient.id)},
        )
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "DNI_CONFLICT", "message": "Ya existe un paciente con ese DNI."},
        ) from exc

    password = await _provision_app_account(db, patient, email, full_name)
    await auth_repo.log_audit_event(
        db,
        AuditEventType.PATIENT_ACCOUNT_CREATED,
        user_id=input_data.requesting_user.id,
        metadata={"target_patient_id": str(patient.id), "user_id": str(patient.user_id)},
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _email_conflict() from exc

    row = await repo.get_patient_row(db, patient.id, input_data.doctor_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Created patient not found")
    # La contraseña viaja solo acá. Auth0 guarda el hash: ningún endpoint la
    # puede volver a leer, y por eso el portal la muestra con botón de copiar.
    return PatientCreateOut(**_patient_out(row).model_dump(), generatedPassword=password)


async def update_patient(input_data: PatientUpdateInput, db: AsyncSession) -> PatientOut:
    patient = await repo.get_patient_model_for_update(
        db, input_data.patient_id, input_data.doctor_id
    )
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )

    update_data = input_data.data.model_dump(exclude_unset=True)
    for field in ("dni", "fullName", "birthDate", "sex"):
        if field in update_data and update_data[field] is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_FIELD", "message": f"El campo {field} no puede ser nulo."},
            )

    if "dni" in update_data and update_data["dni"] != patient.dni:
        existing = await repo.get_patient_by_dni(
            db, update_data["dni"], exclude_patient_id=patient.id
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "DNI_CONFLICT", "message": "Ya existe un paciente con ese DNI."},
            )
        patient.dni = update_data["dni"]
        patient.medical_record_num = update_data["dni"]

    if "fullName" in update_data:
        patient.first_name, patient.last_name = _split_full_name(update_data["fullName"])
    if "birthDate" in update_data:
        patient.date_of_birth = update_data["birthDate"]
    if "sex" in update_data:
        patient.sex = update_data["sex"]
    if "contactEmail" in update_data:
        # El email del paciente **es** su usuario en la app: cambiarlo acá sin
        # tocar Auth0 dejaría al paciente sin poder loguearse con lo que la
        # ficha muestra.
        patient.email = await _sync_account_email(db, patient, update_data["contactEmail"])
    if "contactPhone" in update_data:
        patient.phone = update_data["contactPhone"]

    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.PATIENT_UPDATED,
            user_id=input_data.actor_id,
            metadata={"target_patient_id": str(patient.id)},
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "DNI_CONFLICT", "message": "Ya existe un paciente con ese DNI."},
        ) from exc
    row = await repo.get_patient_row(db, patient.id, input_data.doctor_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Updated patient not found")
    return _patient_out(row)


async def _sync_account_email(db: AsyncSession, patient: Patient, new_email: str | None) -> str:
    """Propaga el email nuevo a la cuenta de la app y lo devuelve normalizado."""
    if new_email is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EMAIL_REQUIRED",
                "message": "El email no puede quedar vacío.",
                "fields": {"contactEmail": "Requerido."},
            },
        )
    email = new_email.lower()
    if email == patient.email:
        return email

    user = (
        await users_repo.get_user_by_id(db, patient.user_id)
        if patient.user_id is not None
        else None
    )
    if user is None:
        return email
    if await users_repo.get_user_by_email_any(db, email) is not None:
        raise _email_conflict()
    if user.auth0_id is not None:
        try:
            await update_auth0_user_email(user.auth0_id, email)
        except Auth0Error as exc:
            raise HTTPException(
                status_code=exc.status, detail={"code": exc.code, "message": exc.message}
            ) from exc
    user.email = email
    return email


async def delete_patient(input_data: PatientIdInput, db: AsyncSession) -> None:
    patient = await repo.get_patient_model_for_update(
        db, input_data.patient_id, input_data.doctor_id
    )
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    # Antes de la baja: `soft_delete_patient` desasigna los equipos, y con el
    # `patient_id` en null ya no queda rastro de a qué paciente pertenecían los
    # estudios abiertos. Se cancelan, no se completan: la baja del paciente no
    # es el final normal de un Holter.
    await studies.close_open_studies_for_patient(
        db, patient, input_data.actor_id, "patient_deleted"
    )
    # La cuenta de la app se cierra en el mismo acto. Sin esto el paciente
    # seguiría logueándose y recibiendo avisos de un estudio que ya no existe.
    await _revoke_app_account(db, patient)
    await repo.soft_delete_patient(db, patient)
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.PATIENT_DELETED,
            user_id=input_data.actor_id,
            metadata={"target_patient_id": str(patient.id)},
        )
    await db.commit()


async def get_patient_device(input_data: PatientIdInput, db: AsyncSession) -> HolterHealthOut:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )

    device = await repo.get_assigned_device_for_patient(db, patient.id)
    if device is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DEVICE_NOT_FOUND", "message": "Paciente sin Holter asignado."},
        )
    # Mismo helper que GET /devices/{id}/health: el FE consume el mismo tipo
    # HolterHealth desde los dos endpoints y los valores tienen que coincidir.
    return holter_health_out(device)


async def get_patient_summary(
    input_data: PatientSummaryInput, db: AsyncSession
) -> PatientSummaryOut:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    return PatientSummaryOut(
        windowHours=input_data.window_hours,
        heartRate=None,
        eventsDetected=None,
        adherencePercent=None,
    )


# --------------------------------------------------------------------------- #
# Acceso a la app móvil
# --------------------------------------------------------------------------- #


async def _patient_for_account(
    input_data: PatientIdInput, db: AsyncSession
) -> tuple[Patient, User]:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    user = (
        await users_repo.get_user_by_id(db, patient.user_id)
        if patient.user_id is not None
        else None
    )
    if user is None or user.auth0_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_APP_ACCOUNT",
                "message": "Este paciente todavía no tiene acceso a la app.",
            },
        )
    return patient, user


async def regenerate_app_password(
    input_data: PatientIdInput, db: AsyncSession
) -> PatientPasswordOut:
    """Genera una contraseña nueva y la devuelve **una sola vez**.

    Es la única forma de que el médico le devuelva el acceso a un paciente que
    la perdió: Auth0 guarda el hash, así que la anterior no se puede leer. Que
    sea "regenerar" y no "ver" es lo que evita tener credenciales de pacientes
    recuperables desde la base.
    """
    patient, user = await _patient_for_account(input_data, db)
    if user.auth0_id is None:  # pragma: no cover - `_patient_for_account` ya lo garantiza
        raise HTTPException(status_code=409, detail={"code": "NO_APP_ACCOUNT", "message": ""})

    password = generate_patient_password()
    try:
        await update_auth0_user_password(user.auth0_id, password)
    except Auth0Error as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc

    # La contraseña vieja deja de servir, pero el token que ya emitimos seguiría
    # vivo hasta 60 días: `session_version` los mata a todos ahora.
    await auth_repo.set_last_logout(db, user.id)
    await patient_app_repo.deactivate_push_tokens_for_user(db, user.id)
    await auth_repo.log_audit_event(
        db,
        AuditEventType.PATIENT_PASSWORD_RESET,
        user_id=input_data.actor_id,
        metadata={"target_patient_id": str(patient.id), "method": "regenerated"},
    )
    await db.commit()
    return PatientPasswordOut(password=password)


async def send_app_password_reset(input_data: PatientIdInput, db: AsyncSession) -> None:
    """Dispara el mail de recuperación de Auth0 al email del paciente."""
    patient, user = await _patient_for_account(input_data, db)
    try:
        await trigger_password_reset(user.email)
    except Auth0Error as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc
    await auth_repo.log_audit_event(
        db,
        AuditEventType.PATIENT_PASSWORD_RESET,
        user_id=input_data.actor_id,
        metadata={"target_patient_id": str(patient.id), "method": "email"},
    )
    await db.commit()


async def create_app_account(input_data: PatientIdInput, db: AsyncSession) -> PatientPasswordOut:
    """Da de alta la cuenta de un paciente que todavía no la tiene.

    Existe por los pacientes cargados antes de que la app existiera: la
    migración no les creó cuenta retroactivamente porque muchos ni siquiera
    tienen email. Acá se completa a mano desde la ficha.
    """
    patient = await repo.get_patient_model_for_update(
        db, input_data.patient_id, input_data.doctor_id
    )
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    if patient.user_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "APP_ACCOUNT_EXISTS",
                "message": "El paciente ya tiene acceso a la app.",
            },
        )
    if not patient.email:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EMAIL_REQUIRED",
                "message": "Cargá el email del paciente antes de crearle el acceso.",
                "fields": {"contactEmail": "Requerido para crear la cuenta."},
            },
        )
    if await users_repo.get_user_by_email_any(db, patient.email) is not None:
        raise _email_conflict()

    full_name = f"{patient.first_name} {patient.last_name}".strip()
    password = await _provision_app_account(db, patient, patient.email, full_name)
    await auth_repo.log_audit_event(
        db,
        AuditEventType.PATIENT_ACCOUNT_CREATED,
        user_id=input_data.actor_id,
        metadata={"target_patient_id": str(patient.id), "user_id": str(patient.user_id)},
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _email_conflict() from exc
    return PatientPasswordOut(password=password)


async def _revoke_app_account(db: AsyncSession, patient: Patient) -> None:
    """Deja la cuenta de la app inutilizable, sin borrarla.

    No propaga los errores de Auth0: la baja del paciente ya está decidida y no
    se puede dejar a medias porque el proveedor de identidad no contestó. El
    `is_active=False` local ya corta cualquier token vivo en el próximo request.
    """
    if patient.user_id is None:
        return
    user = await users_repo.get_user_by_id(db, patient.user_id)
    if user is None:
        return
    user.is_active = False
    await auth_repo.set_last_logout(db, user.id)
    await patient_app_repo.deactivate_push_tokens_for_user(db, user.id)
    if user.auth0_id is not None:
        try:
            await block_auth0_user(user.auth0_id)
        except Auth0Error:
            await auth_repo.log_audit_event(
                db,
                AuditEventType.USER_UPDATED,
                metadata={"target_user_id": str(user.id), "error": "AUTH0_BLOCK_FAILED"},
            )
    user.deleted_at = datetime.now(UTC)
    await db.flush()
