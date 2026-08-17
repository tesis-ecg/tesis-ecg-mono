import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.study import StudyStatus
from app.dependencies.auth_dependencies import RoleScope, get_doctor_scope
from app.dependencies.common_dependencies import get_db
from app.modules.studies import studies_service as service
from app.modules.studies.studies_schemas import (
    StudyDetailOut,
    StudyEcgOut,
    StudyIdInput,
    StudyListInput,
    StudyListResponse,
)

router = APIRouter()


@router.get("", response_model=StudyListResponse)
async def list_studies(
    q: str | None = None,
    status_filter: list[StudyStatus] | None = Query(default=None, alias="status"),
    status_bracket: list[StudyStatus] | None = Query(default=None, alias="status[]"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyListResponse:
    return await service.list_studies(
        StudyListInput(
            doctor_id=scope.doctor_id,
            q=q,
            status=status_filter or status_bracket,
            limit=limit,
            offset=offset,
        ),
        db,
    )


@router.get("/{study_id}", response_model=StudyDetailOut)
async def get_study(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyDetailOut:
    return await service.get_study(StudyIdInput(doctor_id=scope.doctor_id, study_id=study_id), db)


@router.get("/{study_id}/ecg", response_model=StudyEcgOut)
async def get_study_ecg(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyEcgOut:
    return await service.get_study_ecg(
        StudyIdInput(doctor_id=scope.doctor_id, study_id=study_id), db
    )
