import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.patient import PatientStudyStatus
from app.dependencies.auth_dependencies import RoleScope, get_doctor_scope
from app.dependencies.common_dependencies import get_db
from app.modules.devices.devices_schemas import HolterHealthOut
from app.modules.patients import patients_service as service
from app.modules.patients.patients_schemas import (
    PatientCreateInput,
    PatientCreateOut,
    PatientCreateRequest,
    PatientIdInput,
    PatientListInput,
    PatientListResponse,
    PatientOut,
    PatientPasswordOut,
    PatientSummaryInput,
    PatientSummaryOut,
    PatientUpdateInput,
    PatientUpdateRequest,
)
from app.modules.studies import studies_service
from app.modules.studies.studies_schemas import PatientStudiesInput, PatientStudiesResponse

router = APIRouter()


@router.get("", response_model=PatientListResponse)
async def list_patients(
    q: str | None = Query(default=None, max_length=120),
    has_device: bool | None = Query(default=None, alias="hasDevice"),
    status_filter: list[PatientStudyStatus] | None = Query(default=None, alias="status"),
    status_bracket: list[PatientStudyStatus] | None = Query(default=None, alias="status[]"),
    # le=250: el diálogo de reasignación del FE pide limit=250 para filtrar los
    # pacientes sin Holter en el cliente (useUnassignedPatients).
    limit: int = Query(default=20, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="name", pattern="^(name|lastDataReceivedAt)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientListResponse:
    return await service.list_patients(
        PatientListInput(
            doctor_id=scope.doctor_id,
            q=q,
            status=status_filter or status_bracket,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            has_device=has_device,
        ),
        db,
    )


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    return await service.get_patient(
        PatientIdInput(doctor_id=scope.doctor_id, patient_id=patient_id), db
    )


@router.get("/{patient_id}/studies", response_model=PatientStudiesResponse)
async def get_patient_studies(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientStudiesResponse:
    return await studies_service.list_patient_studies(
        PatientStudiesInput(doctor_id=scope.doctor_id, patient_id=patient_id), db
    )


@router.get("/{patient_id}/summary", response_model=PatientSummaryOut)
async def get_patient_summary(
    patient_id: uuid.UUID,
    window_hours: int = Query(default=24, ge=1, alias="windowHours"),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientSummaryOut:
    return await service.get_patient_summary(
        PatientSummaryInput(
            doctor_id=scope.doctor_id,
            patient_id=patient_id,
            window_hours=window_hours,
        ),
        db,
    )


@router.get("/{patient_id}/device", response_model=HolterHealthOut)
async def get_patient_device(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterHealthOut:
    return await service.get_patient_device(
        PatientIdInput(doctor_id=scope.doctor_id, patient_id=patient_id), db
    )


@router.post("", response_model=PatientCreateOut, status_code=status.HTTP_201_CREATED)
async def create_patient(
    data: PatientCreateRequest,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientCreateOut:
    """Alta del paciente **y** de su cuenta en la app móvil.

    La respuesta trae `generatedPassword` una sola vez: Auth0 guarda el hash y
    no hay endpoint que la pueda volver a leer. El portal la muestra en un
    diálogo con botón de copiar antes de que se pierda.
    """
    return await service.create_patient(
        PatientCreateInput(doctor_id=scope.doctor_id, requesting_user=scope.user, data=data), db
    )


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: uuid.UUID,
    data: PatientUpdateRequest,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    return await service.update_patient(
        PatientUpdateInput(
            doctor_id=scope.doctor_id,
            patient_id=patient_id,
            data=data,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.post("/{patient_id}/app-account", response_model=PatientPasswordOut)
async def create_patient_app_account(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientPasswordOut:
    """Crea el acceso a la app de un paciente que todavía no lo tiene.

    Para las fichas cargadas antes de que la app existiera: la migración no les
    creó cuenta retroactivamente porque muchas ni siquiera tienen email.
    """
    return await service.create_app_account(
        PatientIdInput(doctor_id=scope.doctor_id, patient_id=patient_id, actor_id=scope.user.id),
        db,
    )


@router.post("/{patient_id}/app-password", response_model=PatientPasswordOut)
async def regenerate_patient_app_password(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> PatientPasswordOut:
    """Genera una contraseña nueva y la devuelve en claro **una sola vez**.

    Mismo criterio que `POST /devices/{id}/api-key`. No existe un endpoint para
    "ver" la contraseña actual porque no existe la contraseña actual: Auth0
    guarda el hash, y guardarla nosotros sería dejar credenciales de pacientes
    recuperables desde la base.
    """
    return await service.regenerate_app_password(
        PatientIdInput(doctor_id=scope.doctor_id, patient_id=patient_id, actor_id=scope.user.id),
        db,
    )


@router.post("/{patient_id}/password-reset", status_code=status.HTTP_204_NO_CONTENT)
async def send_patient_password_reset(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Manda el mail de recuperación de Auth0 al paciente."""
    await service.send_app_password_reset(
        PatientIdInput(doctor_id=scope.doctor_id, patient_id=patient_id, actor_id=scope.user.id),
        db,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_patient(
    patient_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.delete_patient(
        PatientIdInput(
            doctor_id=scope.doctor_id,
            patient_id=patient_id,
            actor_id=scope.user.id,
        ),
        db,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
