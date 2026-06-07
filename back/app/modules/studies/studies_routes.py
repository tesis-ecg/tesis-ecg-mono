import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.doctor import Doctor
from app.dependencies.auth_dependencies import get_current_doctor
from app.dependencies.common_dependencies import get_db
from app.modules.studies import studies_service as service
from app.modules.studies.studies_schemas import StudyDetailOut, StudyEcgOut, StudyIdInput

router = APIRouter()


@router.get("/{study_id}", response_model=StudyDetailOut)
async def get_study(
    study_id: uuid.UUID,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> StudyDetailOut:
    return await service.get_study(StudyIdInput(doctor_id=current_doctor.id, study_id=study_id), db)


@router.get("/{study_id}/ecg", response_model=StudyEcgOut)
async def get_study_ecg(
    study_id: uuid.UUID,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
) -> StudyEcgOut:
    return await service.get_study_ecg(
        StudyIdInput(doctor_id=current_doctor.id, study_id=study_id), db
    )
