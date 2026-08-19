from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast

import boto3
from botocore.config import Config
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.audit_event import AuditEventType
from app.db.models.device import Device
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.modules.auth import auth_repository as auth_repo
from app.modules.studies import studies_repository as repo
from app.modules.studies.studies_schemas import (
    PatientStudiesInput,
    PatientStudiesResponse,
    PatientStudyOut,
    StudyDetailOut,
    StudyEcgLevelOut,
    StudyEcgManifestOut,
    StudyEcgObjectOut,
    StudyEcgOut,
    StudyIdInput,
    StudyListInput,
    StudyListResponse,
)

MAX_LEGACY_ECG_BYTES = 5 * 1024 * 1024


def _duration_ms(study: Study) -> int:
    if study.duration_ms is not None:
        return study.duration_ms
    if study.ended_at is not None:
        return int((study.ended_at - study.started_at).total_seconds() * 1000)
    if study.status == StudyStatus.IN_PROGRESS:
        return max(int((datetime.now(UTC) - study.started_at).total_seconds() * 1000), 0)
    return 0


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


def _study_detail_out(
    study: Study, patient: Patient, device: Device, doctor_name: str | None
) -> StudyDetailOut:
    return StudyDetailOut(
        id=study.id,
        patientId=patient.id,
        patientName=f"{patient.first_name} {patient.last_name}".strip(),
        startedAt=study.started_at,
        endedAt=study.ended_at,
        durationMs=_duration_ms(study),
        deviceSerial=device.serial_number,
        status=study.status,
        doctorId=patient.doctor_id,
        doctorName=doctor_name,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "STUDY_NOT_FOUND", "message": "Estudio no encontrado."},
    )


@lru_cache
def _get_s3_client() -> Any:
    """Cliente usado para firmar las URLs que descarga el navegador.

    Se firma contra `s3_public_endpoint_url` (el host al que llega el browser)
    porque SigV4 incluye el header `Host` en la firma — reescribir el host de la
    URL después de firmarla la invalida.
    """
    endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url
    client_kwargs: dict[str, object] = {
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
        "region_name": settings.aws_region,
        "config": Config(signature_version="s3v4"),
    }
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **client_kwargs)


def _build_presigned_ecg_url(key: str) -> str:
    return cast(
        str,
        _get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=settings.s3_presign_expire_seconds,
        ),
    )


async def list_studies(input_data: StudyListInput, db: AsyncSession) -> StudyListResponse:
    rows, total = await repo.list_studies(
        db,
        doctor_id=input_data.doctor_id,
        q=input_data.q,
        statuses=input_data.status,
        limit=input_data.limit,
        offset=input_data.offset,
    )
    return StudyListResponse(
        items=[
            _study_detail_out(study, patient, device, doctor_name)
            for study, patient, device, doctor_name in rows
        ],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
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
    study, patient, device, doctor_name = result
    return _study_detail_out(study, patient, device, doctor_name)


async def get_study_ecg(input_data: StudyIdInput, db: AsyncSession) -> StudyEcgOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _ = result
    if study.ecg_s3_key is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ECG_NOT_FOUND", "message": "ECG no disponible para este estudio."},
        )
    byte_length = study.ecg_byte_length or study.samples_count * 4
    if byte_length > MAX_LEGACY_ECG_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "ECG_LEGACY_TOO_LARGE",
                "message": "Este ECG requiere el protocolo de manifest.",
            },
        )
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.s3_presign_expire_seconds)
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_ACCESSED,
            user_id=input_data.actor_id,
            metadata={"target_study_id": str(study.id), "protocol": "legacy"},
        )
        await db.commit()
    return StudyEcgOut(
        url=_build_presigned_ecg_url(study.ecg_s3_key),
        sampleRate=study.sample_rate,
        startTimestamp=int(study.started_at.timestamp() * 1000),
        durationMs=_duration_ms(study),
        sampleCount=study.samples_count,
        expiresAt=expires_at,
    )


async def get_study_ecg_manifest(input_data: StudyIdInput, db: AsyncSession) -> StudyEcgManifestOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _ = result
    if study.ecg_s3_key is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ECG_NOT_FOUND", "message": "ECG no disponible para este estudio."},
        )

    expires_at = datetime.now(UTC) + timedelta(seconds=settings.s3_presign_expire_seconds)
    byte_length = study.ecg_byte_length or study.samples_count * 4
    levels = [
        StudyEcgLevelOut(
            url=_build_presigned_ecg_url(str(level["key"])),
            expiresAt=expires_at,
            byteLength=int(level["byteLength"]),
            sha256=str(level["sha256"]),
            samplesPerBucket=int(level["samplesPerBucket"]),
            pointCount=int(level["pointCount"]),
        )
        for level in study.ecg_pyramid_levels
    ]
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_ACCESSED,
            user_id=input_data.actor_id,
            metadata={"target_study_id": str(study.id), "protocol": "manifest-v1"},
        )
        await db.commit()
    return StudyEcgManifestOut(
        encoding=study.ecg_encoding,
        sampleRate=study.sample_rate,
        sampleCount=study.samples_count,
        startTimestamp=int(study.started_at.timestamp() * 1000),
        durationMs=_duration_ms(study),
        raw=StudyEcgObjectOut(
            url=_build_presigned_ecg_url(study.ecg_s3_key),
            expiresAt=expires_at,
            byteLength=byte_length,
            sha256=study.ecg_sha256,
        ),
        levels=levels,
    )
