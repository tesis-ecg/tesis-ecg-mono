import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.doctor import Doctor
from app.db.models.patient import PatientStudyStatus
from app.dependencies.auth_dependencies import get_current_doctor
from app.dependencies.common_dependencies import get_db
from app.modules.devices.devices_schemas import HolterHealthOut
from app.modules.patients import patients_service as service
from app.modules.patients.patients_schemas import (
    PatientCreateInput,
    PatientCreateRequest,
    PatientIdInput,
    PatientListInput,
    PatientListResponse,
    PatientOut,
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
    q: str | None = None,
    status_filter: list[PatientStudyStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="name", pattern="^(name|lastDataReceivedAt)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> PatientListResponse:
    return await service.list_patients(
        PatientListInput(
            doctor_id=current_doctor.id,
            q=q,
            status=status_filter,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
        ),
        db,
    )


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: uuid.UUID,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    return await service.get_patient(
        PatientIdInput(doctor_id=current_doctor.id, patient_id=patient_id), db
    )


@router.get("/{patient_id}/studies", response_model=PatientStudiesResponse)
async def get_patient_studies(
    patient_id: uuid.UUID,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> PatientStudiesResponse:
    return await studies_service.list_patient_studies(
        PatientStudiesInput(doctor_id=current_doctor.id, patient_id=patient_id), db
    )


@router.get("/{patient_id}/summary", response_model=PatientSummaryOut)
async def get_patient_summary(
    patient_id: uuid.UUID,
    window_hours: int = Query(default=24, ge=1, alias="windowHours"),
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> PatientSummaryOut:
    return await service.get_patient_summary(
        PatientSummaryInput(
            doctor_id=current_doctor.id,
            patient_id=patient_id,
            window_hours=window_hours,
        ),
        db,
    )


@router.get("/{patient_id}/device", response_model=HolterHealthOut)
async def get_patient_device(
    patient_id: uuid.UUID,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> HolterHealthOut:
    return await service.get_patient_device(
        PatientIdInput(doctor_id=current_doctor.id, patient_id=patient_id), db
    )


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def create_patient(
    data: PatientCreateRequest,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    return await service.create_patient(
        PatientCreateInput(doctor_id=current_doctor.id, data=data), db
    )


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: uuid.UUID,
    data: PatientUpdateRequest,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    return await service.update_patient(
        PatientUpdateInput(doctor_id=current_doctor.id, patient_id=patient_id, data=data), db
    )


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_patient(
    patient_id: uuid.UUID,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.delete_patient(
        PatientIdInput(doctor_id=current_doctor.id, patient_id=patient_id), db
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
