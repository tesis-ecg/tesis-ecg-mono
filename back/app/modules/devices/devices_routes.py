import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import DeviceStatus
from app.db.models.user import User
from app.dependencies.auth_dependencies import get_current_user
from app.dependencies.common_dependencies import get_db
from app.modules.devices import devices_service as service
from app.modules.devices.devices_schemas import (
    AssignHolterInput,
    AssignHolterRequest,
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
    q: str | None = None,
    status_filter: list[DeviceStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterListResponse:
    return await service.list_holters(
        HolterListInput(q=q, status=status_filter, limit=limit, offset=offset), db
    )


@router.post("", response_model=HolterCreateOut, status_code=status.HTTP_201_CREATED)
async def create_holter(
    data: HolterCreateRequest,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterCreateOut:
    return await service.create_holter(HolterCreateInput(data=data), db)


@router.get("/{device_id}", response_model=HolterOut)
async def get_holter(
    device_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.get_holter(HolterIdInput(device_id=device_id), db)


@router.patch("/{device_id}", response_model=HolterOut)
async def update_holter(
    device_id: uuid.UUID,
    data: HolterUpdateRequest,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.update_holter(HolterUpdateInput(device_id=device_id, data=data), db)


@router.delete("/{device_id}", response_model=HolterOut)
async def delete_holter(
    device_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.delete_holter(HolterIdInput(device_id=device_id), db)


@router.post("/{device_id}/assign", response_model=HolterOut)
async def assign_holter(
    device_id: uuid.UUID,
    data: AssignHolterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.assign_holter(
        AssignHolterInput(
            doctor_id=current_user.id,
            device_id=device_id,
            patient_id=data.patientId,
        ),
        db,
    )


@router.post("/{device_id}/unassign", response_model=HolterOut)
async def unassign_holter(
    device_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.unassign_holter(HolterIdInput(device_id=device_id), db)


@router.post("/{device_id}/reassign", response_model=HolterOut)
async def reassign_holter(
    device_id: uuid.UUID,
    data: ReassignHolterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterOut:
    return await service.reassign_holter(
        AssignHolterInput(
            doctor_id=current_user.id,
            device_id=device_id,
            patient_id=data.patientId,
        ),
        db,
    )


@router.get("/{device_id}/health", response_model=HolterHealthOut)
async def get_holter_health(
    device_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HolterHealthOut:
    return await service.get_holter_health(HolterIdInput(device_id=device_id), db)
