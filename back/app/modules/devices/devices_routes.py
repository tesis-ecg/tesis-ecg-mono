import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import DeviceStatus
from app.db.models.user import User
from app.dependencies.auth_dependencies import (
    RoleScope,
    get_doctor_scope,
    get_role_scope,
    require_admin,
)
from app.dependencies.common_dependencies import get_db
from app.modules.devices import devices_service as service
from app.modules.devices.devices_schemas import (
    AssignDoctorInput,
    AssignDoctorRequest,
    AssignHolterInput,
    AssignHolterRequest,
    HolterApiKeyOut,
    HolterCreateInput,
    HolterCreateOut,
    HolterCreateRequest,
    HolterHealthOut,
    HolterIdInput,
    HolterListInput,
    HolterListResponse,
    HolterOut,
    HolterUpdateInput,
    HolterUpdateRequest,
    ReassignHolterRequest,
)

router = APIRouter()


@router.get("", response_model=HolterListResponse)
async def list_holters(
    q: str | None = Query(default=None, max_length=120),
    status_filter: list[DeviceStatus] | None = Query(default=None, alias="status"),
    status_bracket: list[DeviceStatus] | None = Query(default=None, alias="status[]"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    scope: RoleScope = Depends(get_role_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterListResponse:
    return await service.list_holters(
        HolterListInput(
            doctor_id=scope.doctor_id,
            q=q,
            status=status_filter or status_bracket,
            limit=limit,
            offset=offset,
        ),
        db,
    )


@router.post("", response_model=HolterCreateOut, status_code=status.HTTP_201_CREATED)
async def create_holter(
    data: HolterCreateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterCreateOut:
    return await service.create_holter(HolterCreateInput(data=data, actor_id=current_user.id), db)


@router.get("/{device_id}", response_model=HolterOut)
async def get_holter(
    device_id: uuid.UUID,
    scope: RoleScope = Depends(get_role_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.get_holter(
        HolterIdInput(doctor_id=scope.doctor_id, device_id=device_id), db
    )


@router.patch("/{device_id}", response_model=HolterOut)
async def update_holter(
    device_id: uuid.UUID,
    data: HolterUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.update_holter(
        HolterUpdateInput(device_id=device_id, data=data, actor_id=current_user.id), db
    )


@router.delete("/{device_id}", response_model=HolterOut)
async def delete_holter(
    device_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.delete_holter(
        HolterIdInput(doctor_id=None, device_id=device_id, actor_id=current_user.id), db
    )


@router.post("/{device_id}/assign-doctor", response_model=HolterOut)
async def assign_holter_doctor(
    device_id: uuid.UUID,
    data: AssignDoctorRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.assign_holter_doctor(
        AssignDoctorInput(device_id=device_id, data=data, actor_id=current_user.id), db
    )


@router.post("/{device_id}/unassign-doctor", response_model=HolterOut)
async def unassign_holter_doctor(
    device_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.unassign_holter_doctor(
        HolterIdInput(doctor_id=None, device_id=device_id, actor_id=current_user.id), db
    )


@router.post("/{device_id}/assign", response_model=HolterOut)
async def assign_holter(
    device_id: uuid.UUID,
    data: AssignHolterRequest,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.assign_holter(
        AssignHolterInput(
            doctor_id=scope.doctor_id,
            device_id=device_id,
            patient_id=data.patientId,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.post("/{device_id}/unassign", response_model=HolterOut)
async def unassign_holter(
    device_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.unassign_holter(
        HolterIdInput(doctor_id=scope.doctor_id, device_id=device_id, actor_id=scope.user.id),
        db,
    )


@router.post("/{device_id}/reassign", response_model=HolterOut)
async def reassign_holter(
    device_id: uuid.UUID,
    data: ReassignHolterRequest,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.reassign_holter(
        AssignHolterInput(
            doctor_id=scope.doctor_id,
            device_id=device_id,
            patient_id=data.patientId,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.get("/{device_id}/api-key", response_model=HolterApiKeyOut)
async def get_holter_api_key(
    device_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterApiKeyOut:
    """Devuelve la API key en claro del equipo, para grabarla en su firmware.

    Solo admin: la key habilita a subir señal como ese dispositivo, así que
    entregarla es equivalente a entregar el equipo. Cada lectura queda auditada.
    """
    return await service.get_api_key(
        HolterIdInput(doctor_id=None, device_id=device_id, actor_id=current_user.id), db
    )


@router.post("/{device_id}/api-key", response_model=HolterApiKeyOut)
async def rotate_holter_api_key(
    device_id: uuid.UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HolterApiKeyOut:
    """Rota la API key del equipo: genera una nueva y deja la anterior inútil.

    Solo admin, por lo mismo que el GET. Es inmediato y no se puede deshacer —
    el chaleco que tenga cargada la key vieja empieza a recibir 401.
    """
    return await service.rotate_api_key(
        HolterIdInput(doctor_id=None, device_id=device_id, actor_id=current_user.id), db
    )


@router.get("/{device_id}/health", response_model=HolterHealthOut)
async def get_holter_health(
    device_id: uuid.UUID,
    scope: RoleScope = Depends(get_role_scope),
    db: AsyncSession = Depends(get_db),
) -> HolterHealthOut:
    return await service.get_holter_health(
        HolterIdInput(doctor_id=scope.doctor_id, device_id=device_id), db
    )
