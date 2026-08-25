"""Crea un estudio local, aislado e idempotente, con avisos ECG de ejemplo.

Uso desde la raíz con Docker Compose levantado:

    docker compose exec back python -m app.scripts.seed_ecg_showcase

Reejecutarlo reemplaza únicamente el paciente ``SHOWCASE-ECG-ALERTS`` y los
objetos bajo ``showcases/ecg-alerts/``. Está bloqueado fuera de development/test.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, settings
from app.core.s3 import delete_keys, ensure_bucket, list_keys, put_object
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User, UserRole
from app.db.session import async_session_factory
from app.scripts.seed_demo import PYRAMID_BUCKETS, EventSpec, synth_ecg

SHOWCASE_MRN = "SHOWCASE-ECG-ALERTS"
SHOWCASE_SERIAL = "HOLTER-SHOWCASE-ECG"
SHOWCASE_PREFIX = "showcases/ecg-alerts/"
SAMPLE_RATE = 250
DURATION_SECONDS = 600


@dataclass(frozen=True)
class ShowcaseEvent:
    kind: str
    event_type: ECGEventType
    severity: ECGEventSeverity
    start_s: float
    duration_s: float
    confidence_score: float | None = None


SHOWCASE_EVENTS = (
    ShowcaseEvent("sqi_unanalyzable", ECGEventType.NOISE, ECGEventSeverity.LOW, 60, 12),
    ShowcaseEvent("lead_off", ECGEventType.NOISE, ECGEventSeverity.MEDIUM, 150, 15),
    ShowcaseEvent("tachycardia", ECGEventType.TACHYCARDIA, ECGEventSeverity.HIGH, 240, 45, 0.91),
    ShowcaseEvent("afib", ECGEventType.AFIB, ECGEventSeverity.CRITICAL, 360, 60, 0.97),
    ShowcaseEvent("adc_saturated", ECGEventType.NOISE, ECGEventSeverity.MEDIUM, 385, 10),
    ShowcaseEvent("symptom_marker", ECGEventType.OTHER, ECGEventSeverity.HIGH, 480, 0.04, None),
)

_ALERT_MESSAGE = {
    "tachycardia": "Taquicardia detectada en el estudio showcase.",
    "afib": "Fibrilación auricular detectada en el estudio showcase.",
    "symptom_marker": "El paciente marcó un síntoma en el estudio showcase.",
}


def build_showcase_signal() -> np.ndarray:
    """Señal reproducible con alteraciones visibles en los rangos anotados."""
    rhythm_specs = [
        EventSpec(
            ECGEventType.TACHYCARDIA,
            ECGEventSeverity.HIGH,
            start_s=240,
            duration_s=45,
            bpm=158,
        ),
        EventSpec(
            ECGEventType.AFIB,
            ECGEventSeverity.CRITICAL,
            start_s=360,
            duration_s=60,
            bpm=132,
        ),
    ]
    signal = synth_ecg(
        "ecg-alerts-showcase-v1",
        DURATION_SECONDS,
        base_bpm=72,
        specs=rhythm_specs,
        fs=SAMPLE_RATE,
    )
    rng = np.random.default_rng(20260824)

    sqi = _sample_slice(60, 72)
    signal[sqi] += (0.45 * rng.standard_normal(sqi.stop - sqi.start)).astype(np.float32)

    lead_off = _sample_slice(150, 165)
    signal[lead_off] = (0.015 * rng.standard_normal(lead_off.stop - lead_off.start)).astype(
        np.float32
    )

    saturated = _sample_slice(385, 395)
    saturated_count = saturated.stop - saturated.start
    signal[saturated] = np.where(np.arange(saturated_count) % 2 == 0, 2.4, -2.4).astype(np.float32)
    return signal.astype("<f4", copy=False)


def _sample_slice(start_s: float, end_s: float) -> slice:
    return slice(round(start_s * SAMPLE_RATE), round(end_s * SAMPLE_RATE))


def _pyramid(study_id: uuid.UUID, signal: np.ndarray) -> list[dict[str, int | str]]:
    levels: list[dict[str, int | str]] = []
    for bucket_size in PYRAMID_BUCKETS:
        bucket_count = (signal.size + bucket_size - 1) // bucket_size
        if bucket_count * 2 >= signal.size:
            continue
        envelope = np.empty(bucket_count * 2, dtype="<f4")
        for bucket_index in range(bucket_count):
            bucket = signal[
                bucket_index * bucket_size : min((bucket_index + 1) * bucket_size, signal.size)
            ]
            envelope[bucket_index * 2] = bucket.min()
            envelope[bucket_index * 2 + 1] = bucket.max()
        payload = envelope.tobytes()
        key = f"{SHOWCASE_PREFIX}studies/{study_id}/ecg.minmax.{bucket_size}.f32"
        put_object(key, payload)
        levels.append(
            {
                "key": key,
                "samplesPerBucket": bucket_size,
                "pointCount": int(envelope.size),
                "byteLength": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return levels


async def _doctor(db: AsyncSession, email: str) -> Doctor:
    user = await db.scalar(
        select(User).where(
            User.email == email.lower(),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        raise RuntimeError(f"La cuenta médica activa {email} no existe.")
    if user.role is not UserRole.MEDICO:
        raise RuntimeError(f"La cuenta {email} no tiene rol de médico.")
    doctor = await db.scalar(
        select(Doctor).where(Doctor.user_id == user.id, Doctor.deleted_at.is_(None))
    )
    if doctor is None:
        doctor = Doctor(
            user_id=user.id,
            specialty="Cardiología",
            license_number="MN SHOWCASE",
        )
        db.add(doctor)
        await db.flush()
    return doctor


async def _remove_previous_showcase(db: AsyncSession) -> None:
    patient = await db.scalar(select(Patient).where(Patient.medical_record_num == SHOWCASE_MRN))
    device = await db.scalar(select(Device).where(Device.serial_number == SHOWCASE_SERIAL))
    study_ids = (
        list((await db.scalars(select(Study.id).where(Study.patient_id == patient.id))).all())
        if patient is not None
        else []
    )
    batch_ids = (
        list((await db.scalars(select(ECGBatch.id).where(ECGBatch.study_id.in_(study_ids)))).all())
        if study_ids
        else []
    )
    if patient is not None:
        await db.execute(delete(Alert).where(Alert.patient_id == patient.id))
    if batch_ids:
        await db.execute(delete(ECGEvent).where(ECGEvent.batch_id.in_(batch_ids)))
        await db.execute(delete(ECGBatch).where(ECGBatch.id.in_(batch_ids)))
    if study_ids:
        await db.execute(delete(Study).where(Study.id.in_(study_ids)))
    if device is not None:
        await db.delete(device)
    if patient is not None:
        await db.delete(patient)
    keys = list_keys(SHOWCASE_PREFIX)
    if keys:
        delete_keys(keys)
    await db.flush()


async def seed_showcase(db: AsyncSession, doctor_email: str) -> Study:
    if settings.environment not in {Environment.DEVELOPMENT, Environment.TEST}:
        raise RuntimeError("El showcase ECG solo se puede crear en development/test.")

    ensure_bucket()
    doctor = await _doctor(db, doctor_email)
    await _remove_previous_showcase(db)

    started_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    patient = Patient(
        doctor_id=doctor.id,
        medical_record_num=SHOWCASE_MRN,
        first_name="Demo",
        last_name="Avisos ECG",
        date_of_birth=date(1960, 1, 1),
        dni="99000001",
        sex=PatientSex.X,
        study_status=PatientStudyStatus.COMPLETED,
        notes="Caso sintético para revisar rangos y severidades del visor ECG.",
    )
    db.add(patient)
    await db.flush()

    device = Device(
        serial_number=SHOWCASE_SERIAL,
        model="Holter ECG Austral (showcase)",
        doctor_id=doctor.id,
        api_key_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        firmware_version="showcase-1.0",
        status=DeviceStatus.AVAILABLE,
    )
    db.add(device)
    await db.flush()

    signal = build_showcase_signal()
    raw_payload = signal.tobytes()
    study = Study(
        patient_id=patient.id,
        device_id=device.id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=DURATION_SECONDS),
        duration_ms=DURATION_SECONDS * 1000,
        status=StudyStatus.COMPLETED,
        samples_count=int(signal.size),
        events_count=len(SHOWCASE_EVENTS),
        sample_rate=SAMPLE_RATE,
        is_simulated=True,
    )
    db.add(study)
    await db.flush()

    raw_key = f"{SHOWCASE_PREFIX}studies/{study.id}/ecg.f32"
    put_object(raw_key, raw_payload)
    study.ecg_s3_key = raw_key
    study.ecg_encoding = "float32-le"
    study.ecg_byte_length = len(raw_payload)
    study.ecg_sha256 = hashlib.sha256(raw_payload).hexdigest()
    study.ecg_pyramid_levels = _pyramid(study.id, signal)

    batch = ECGBatch(
        device_id=device.id,
        study_id=study.id,
        received_at=study.ended_at,
        batch_timestamp=int(started_at.timestamp()),
        duration_seconds=DURATION_SECONDS,
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        num_samples=int(signal.size),
        compression_type="raw-f32",
        s3_key=raw_key,
        file_size_bytes=len(raw_payload),
        processing_status=ProcessingStatus.DONE,
        firmware_version=device.firmware_version,
    )
    db.add(batch)
    await db.flush()

    for spec in SHOWCASE_EVENTS:
        start_sample = round(spec.start_s * SAMPLE_RATE)
        sample_count = max(round(spec.duration_s * SAMPLE_RATE), 1)
        event = ECGEvent(
            batch_id=batch.id,
            event_type=spec.event_type,
            severity=spec.severity,
            timestamp_in_recording=spec.start_s,
            duration_seconds=spec.duration_s,
            confidence_score=spec.confidence_score,
            event_metadata={
                "kind": spec.kind,
                "studyId": str(study.id),
                "startSampleIndex": start_sample,
                "sampleCount": sample_count,
                "source": "showcase",
            },
        )
        db.add(event)
        await db.flush()
        message = _ALERT_MESSAGE.get(spec.kind)
        if message is not None:
            db.add(
                Alert(
                    patient_id=patient.id,
                    event_id=event.id,
                    severity=AlertSeverity[spec.severity.name],
                    message=message,
                    seen_at=None,
                )
            )

    patient.last_data_received_at = study.ended_at
    await db.commit()
    return study


async def _run(doctor_email: str) -> None:
    async with async_session_factory() as db:
        study = await seed_showcase(db, doctor_email)
    print(f"SHOWCASE_STUDY_ID={study.id}")
    print(f"SHOWCASE_ROUTE=/studies/{study.id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el estudio showcase de avisos ECG.")
    parser.add_argument(
        "--doctor-email",
        default="dev@tesis.com",
        help="Médico dueño del showcase (default: dev@tesis.com).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.doctor_email))
    except RuntimeError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
