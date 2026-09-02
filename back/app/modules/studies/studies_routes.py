import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.study import StudyStatus
from app.dependencies.auth_dependencies import RoleScope, get_doctor_scope
from app.dependencies.common_dependencies import get_db
from app.modules.studies import studies_service as service
from app.modules.studies.studies_schemas import (
    SimulateAnomalyInput,
    SimulateAnomalyOut,
    SimulateAnomalyRequest,
    StudyDetailOut,
    StudyEcgManifestOut,
    StudyEcgOut,
    StudyIdInput,
    StudyListInput,
    StudyListResponse,
    StudyPatientReportsResponse,
)

router = APIRouter()


@router.get("", response_model=StudyListResponse)
async def list_studies(
    q: str | None = Query(default=None, max_length=120),
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


@router.post("/{study_id}/complete", response_model=StudyDetailOut)
async def complete_study(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyDetailOut:
    """Cierra el estudio: fija `endedAt` y lo pasa a `completed`.

    Es la contraparte del alta automática que hace la ingesta. Sin esto un
    estudio abierto no tenía forma de terminar.
    """
    return await service.complete_study(
        StudyIdInput(
            doctor_id=scope.doctor_id,
            study_id=study_id,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.post("/{study_id}/cancel", response_model=StudyDetailOut)
async def cancel_study(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyDetailOut:
    """Descarta el estudio: colocación fallida, datos de banco, error de carga."""
    return await service.cancel_study(
        StudyIdInput(
            doctor_id=scope.doctor_id,
            study_id=study_id,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.post("/{study_id}/simulate-anomaly", response_model=SimulateAnomalyOut)
async def simulate_anomaly(
    study_id: uuid.UUID,
    data: SimulateAnomalyRequest,
    background: BackgroundTasks,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> SimulateAnomalyOut:
    """Banco de pruebas: fabrica un hallazgo clínico y notifica al paciente.

    Solo admin y solo fuera de preview/producción — escribe un `ecg_event` y una
    `alert` reales sobre la historia de un paciente. Es el reemplazo temporal
    del pipeline de `app/ml/`, que todavía son stubs, y lo que permite ejercitar
    el aviso de anomalía, el formulario de la bitácora y la respuesta sobre el
    ECG sin hardware.
    """
    if not scope.is_admin:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Requiere rol de administrador."},
        )
    return await service.simulate_anomaly(
        SimulateAnomalyInput(
            doctor_id=scope.doctor_id,
            study_id=study_id,
            actor_id=scope.user.id,
            data=data,
        ),
        db,
        background,
    )


@router.get("/{study_id}/ecg", response_model=StudyEcgOut, deprecated=True)
async def get_study_ecg(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyEcgOut:
    return await service.get_study_ecg(
        StudyIdInput(
            doctor_id=scope.doctor_id,
            study_id=study_id,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.get("/{study_id}/ecg/manifest", response_model=StudyEcgManifestOut)
async def get_study_ecg_manifest(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyEcgManifestOut:
    return await service.get_study_ecg_manifest(
        StudyIdInput(
            doctor_id=scope.doctor_id,
            study_id=study_id,
            actor_id=scope.user.id,
        ),
        db,
    )


@router.get("/{study_id}/patient-reports", response_model=StudyPatientReportsResponse)
async def list_study_patient_reports(
    study_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> StudyPatientReportsResponse:
    """Bitácora del paciente para este estudio.

    Incluye los registros que todavía no tienen señal debajo (`visibleInChart`
    en `false`): el paciente puede marcar un síntoma una hora antes de que el
    chaleco suba ese tramo, y hasta entonces no hay dónde pintarlo en el ECG.
    """
    return await service.list_study_patient_reports(
        StudyIdInput(doctor_id=scope.doctor_id, study_id=study_id), db
    )
