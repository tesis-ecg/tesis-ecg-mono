from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.dependencies.auth_dependencies import require_admin
from app.dependencies.common_dependencies import get_db
from app.modules.doctors import doctors_service as service
from app.modules.doctors.doctors_schemas import DoctorListInput, DoctorOptionOut

router = APIRouter()


@router.get("", response_model=list[DoctorOptionOut])
async def list_doctors(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[DoctorOptionOut]:
    return await service.list_doctor_options(DoctorListInput(), db)
