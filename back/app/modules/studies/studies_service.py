from functools import lru_cache
from typing import Any, cast

import boto3
from botocore.config import Config
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.device import Device
from app.db.models.patient import Patient
from app.db.models.study import Study
from app.modules.studies import studies_repository as repo
from app.modules.studies.studies_schemas import (
    PatientStudiesInput,
    PatientStudiesResponse,
    PatientStudyOut,
    StudyDetailOut,
    StudyEcgOut,
    StudyIdInput,
)


def _duration_ms(study: Study) -> int:
    if study.duration_ms is not None:
        return study.duration_ms
    if study.ended_at is None:
        return 0
    return int((study.ended_at - study.started_at).total_seconds() * 1000)


def _duration_hours(study: Study) -> float | None:
    duration = _duration_ms(study)
    if duration == 0 and study.ended_at is None:
        return None
    return round(duration / 3_600_000, 2)


def _patient_study_out(study: Study) -> PatientStudyOut:
    return PatientStudyOut(
        id=study.id,
        patientId=study.patient_id,
        startedAt=study.started_at,
        endedAt=study.ended_at,
        durationHours=_duration_hours(study),
        status=study.status,
        deviceId=study.device_id,
        samplesCount=study.samples_count,
        eventsCount=study.events_count,
    )


def _study_detail_out(study: Study, patient: Patient, device: Device) -> StudyDetailOut:
    return StudyDetailOut(
        id=study.id,
        patientId=patient.id,
        patientName=f"{patient.first_name} {patient.last_name}".strip(),
        startedAt=study.started_at,
        endedAt=study.ended_at,
        durationMs=_duration_ms(study),
        deviceSerial=device.serial_number,
        status=study.status,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "STUDY_NOT_FOUND", "message": "Estudio no encontrado."},
    )


@lru_cache
def _get_s3_client() -> Any:
    client_kwargs: dict[str, object] = {
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
        "region_name": settings.aws_region,
        "config": Config(signature_version="s3v4"),
    }
    if settings.s3_endpoint_url:
        client_kwargs["endpoint_url"] = settings.s3_endpoint_url
    return boto3.client("s3", **client_kwargs)


def _build_presigned_ecg_url(key: str) -> str:
    return cast(
        str,
        _get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=3600,
        ),
    )


async def list_patient_studies(
    input_data: PatientStudiesInput, db: AsyncSession
) -> PatientStudiesResponse:
    result = await repo.list_for_patient(db, input_data.patient_id, input_data.doctor_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    studies, total = result
    return PatientStudiesResponse(
        items=[_patient_study_out(study) for study in studies], total=total
    )


async def get_study(input_data: StudyIdInput, db: AsyncSession) -> StudyDetailOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, patient, device = result
    return _study_detail_out(study, patient, device)


async def get_study_ecg(input_data: StudyIdInput, db: AsyncSession) -> StudyEcgOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _ = result
    if study.ecg_s3_key is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ECG_NOT_FOUND", "message": "ECG no disponible para este estudio."},
        )
    return StudyEcgOut(
        url=_build_presigned_ecg_url(study.ecg_s3_key),
        sampleRate=study.sample_rate,
        startTimestamp=int(study.started_at.timestamp() * 1000),
        durationMs=_duration_ms(study),
        sampleCount=study.samples_count,
    )
