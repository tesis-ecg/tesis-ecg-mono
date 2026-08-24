"""Carga datos de demo en la base: pacientes, Holters, estudios con ECG y alertas.

El ECG se sintetiza (P-QRS-T + ruido + arritmias inyectadas) y se sube a S3/MinIO
como float32 little-endian crudo, que es exactamente lo que espera el visor del
front (`front/src/features/ecg/api/ecgApi.ts`: `new Float32Array(buffer)` con
`sampleCount * 4` bytes).

Todo lo que crea queda marcado con el prefijo `DEMO-` (historia clínica de los
pacientes) y `HOLTER-DEMO-` (serial de los dispositivos), así que `--reset`
puede borrarlo sin tocar datos reales.

Uso (con el stack de docker compose levantado, desde la raíz del repo):

    docker compose exec back python -m app.scripts.seed_demo
    docker compose exec back python -m app.scripts.seed_demo --reset

Fuera de Docker (requiere que DATABASE_URL y S3_ENDPOINT_URL apunten al host):

    cd back && uv run --group seed python -m app.scripts.seed_demo

Los pacientes se cuelgan del médico `--doctor-email` (default `dev@tesis.com`).
El usuario debe estar previamente habilitado para poder iniciar sesión.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, settings
from app.core.s3 import delete_keys, ensure_bucket, get_s3_client, put_object
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User, UserRole
from app.db.session import async_session_factory

MRN_PREFIX = "DEMO-"
SERIAL_PREFIX = "HOLTER-DEMO-"
SAMPLE_RATE = 250
SD_TOTAL_MB = 128

NOW = datetime.now(UTC).replace(microsecond=0)
PYRAMID_BUCKETS = (16, 64, 256, 1024, 4096, 16384)


# --------------------------------------------------------------------------- #
# Perfiles de datos
# --------------------------------------------------------------------------- #


@dataclass
class EventSpec:
    """Arritmia inyectada en la señal y persistida como `ecg_event` + `alert`."""

    type: ECGEventType
    severity: ECGEventSeverity
    start_s: float
    duration_s: float
    bpm: float | None = None


@dataclass
class StudySpec:
    status: StudyStatus
    starts_hours_ago: float
    minutes: float
    base_bpm: float = 72
    events: list[EventSpec] = field(default_factory=list)


@dataclass
class PatientSpec:
    first_name: str
    last_name: str
    dni: str
    birth_date: date
    sex: PatientSex
    study_status: PatientStudyStatus
    phone: str | None
    email: str | None
    notes: str | None
    device: bool
    battery_pct: int | None = None
    sd_free_mb: int | None = None
    firmware: str = "1.2.0"
    studies: list[StudySpec] = field(default_factory=list)


PATIENTS: list[PatientSpec] = [
    PatientSpec(
        first_name="Lucía",
        last_name="Fernández",
        dni="10254877",
        birth_date=date(1958, 3, 14),
        sex=PatientSex.F,
        study_status=PatientStudyStatus.ACTIVE,
        phone="+54 9 11 4455-1122",
        email="lucia.fernandez@example.com",
        notes="Fibrilación auricular paroxística conocida. Anticoagulada con apixabán.",
        device=True,
        battery_pct=76,
        sd_free_mb=118,
        studies=[
            StudySpec(
                status=StudyStatus.COMPLETED,
                starts_hours_ago=72,
                minutes=12,
                base_bpm=78,
                events=[
                    EventSpec(ECGEventType.AFIB, ECGEventSeverity.HIGH, 180, 95, bpm=118),
                    EventSpec(ECGEventType.PVC, ECGEventSeverity.LOW, 420, 2),
                ],
            ),
            StudySpec(
                status=StudyStatus.IN_PROGRESS,
                starts_hours_ago=3,
                minutes=10,
                base_bpm=82,
                events=[
                    EventSpec(ECGEventType.AFIB, ECGEventSeverity.CRITICAL, 240, 140, bpm=132),
                ],
            ),
        ],
    ),
    PatientSpec(
        first_name="Jorge",
        last_name="Medina",
        dni="8123994",
        birth_date=date(1951, 11, 2),
        sex=PatientSex.M,
        study_status=PatientStudyStatus.ACTIVE,
        phone="+54 9 11 6677-8890",
        email="jorge.medina@example.com",
        notes="Hipertenso. Refiere palpitaciones al esfuerzo.",
        device=True,
        battery_pct=41,
        sd_free_mb=92,
        studies=[
            StudySpec(
                status=StudyStatus.IN_PROGRESS,
                starts_hours_ago=6,
                minutes=14,
                base_bpm=88,
                events=[
                    EventSpec(ECGEventType.TACHYCARDIA, ECGEventSeverity.HIGH, 300, 120, bpm=158),
                    EventSpec(ECGEventType.PVC, ECGEventSeverity.MEDIUM, 600, 2),
                ],
            )
        ],
    ),
    PatientSpec(
        first_name="Marta",
        last_name="Suárez",
        dni="21445300",
        birth_date=date(1966, 6, 30),
        sex=PatientSex.F,
        study_status=PatientStudyStatus.COMPLETED,
        phone="+54 9 341 512-7788",
        email="marta.suarez@example.com",
        notes="Estudio de 24 h completado. Pendiente informe del cardiólogo.",
        device=False,
        studies=[
            StudySpec(
                status=StudyStatus.COMPLETED,
                starts_hours_ago=168,
                minutes=12,
                base_bpm=70,
                events=[EventSpec(ECGEventType.PVC, ECGEventSeverity.LOW, 500, 2)],
            )
        ],
    ),
    PatientSpec(
        first_name="Ricardo",
        last_name="Álvarez",
        dni="7554120",
        birth_date=date(1949, 1, 22),
        sex=PatientSex.M,
        study_status=PatientStudyStatus.PAUSED,
        phone="+54 9 11 2244-9911",
        email=None,
        notes="Bradicardia sinusal. Evaluación de marcapasos en curso.",
        device=True,
        battery_pct=12,
        sd_free_mb=34,
        firmware="1.1.4",
        studies=[
            StudySpec(
                status=StudyStatus.COMPLETED,
                starts_hours_ago=96,
                minutes=11,
                base_bpm=54,
                events=[
                    EventSpec(ECGEventType.BRADYCARDIA, ECGEventSeverity.MEDIUM, 120, 180, bpm=41),
                    EventSpec(ECGEventType.PAUSE, ECGEventSeverity.CRITICAL, 400, 3.2),
                ],
            )
        ],
    ),
    PatientSpec(
        first_name="Ana",
        last_name="Torres",
        dni="30987112",
        birth_date=date(1984, 9, 8),
        sex=PatientSex.F,
        study_status=PatientStudyStatus.NONE,
        phone="+54 9 11 3355-0077",
        email="ana.torres@example.com",
        notes="Alta reciente. Todavía sin Holter asignado.",
        device=False,
        studies=[],
    ),
    PatientSpec(
        first_name="Carlos",
        last_name="Duarte",
        dni="26110455",
        birth_date=date(1977, 4, 17),
        sex=PatientSex.M,
        study_status=PatientStudyStatus.ACTIVE,
        phone="+54 9 351 448-2200",
        email="carlos.duarte@example.com",
        notes="Deportista amateur. Control por síncope durante ejercicio.",
        device=True,
        battery_pct=93,
        sd_free_mb=126,
        studies=[
            StudySpec(
                status=StudyStatus.IN_PROGRESS,
                starts_hours_ago=1.5,
                minutes=9,
                base_bpm=64,
                events=[EventSpec(ECGEventType.NOISE, ECGEventSeverity.LOW, 200, 8)],
            )
        ],
    ),
    PatientSpec(
        first_name="Sofía",
        last_name="Ramírez",
        dni="35220981",
        birth_date=date(1991, 12, 5),
        sex=PatientSex.F,
        study_status=PatientStudyStatus.NONE,
        phone="+54 9 11 7788-3344",
        email="sofia.ramirez@example.com",
        notes="Estudio agendado para la semana próxima.",
        device=True,
        battery_pct=100,
        sd_free_mb=128,
        studies=[StudySpec(status=StudyStatus.SCHEDULED, starts_hours_ago=-72, minutes=0)],
    ),
    PatientSpec(
        first_name="Héctor",
        last_name="Blanco",
        dni="12009873",
        birth_date=date(1962, 8, 26),
        sex=PatientSex.X,
        study_status=PatientStudyStatus.COMPLETED,
        phone=None,
        email="hector.blanco@example.com",
        notes="Primer intento cancelado por mala adherencia de los electrodos.",
        device=False,
        studies=[
            StudySpec(status=StudyStatus.CANCELLED, starts_hours_ago=240, minutes=0),
            StudySpec(
                status=StudyStatus.COMPLETED,
                starts_hours_ago=200,
                minutes=10,
                base_bpm=75,
                events=[
                    EventSpec(ECGEventType.TACHYCARDIA, ECGEventSeverity.MEDIUM, 260, 70, bpm=142)
                ],
            ),
        ],
    ),
]

# Holters sueltos (sin paciente) para poblar el ABM de dispositivos del admin.
SPARE_DEVICES: list[tuple[DeviceStatus, str, int | None, int | None]] = [
    (DeviceStatus.AVAILABLE, "1.2.0", 100, 128),
    (DeviceStatus.AVAILABLE, "1.2.0", 88, 128),
    (DeviceStatus.AVAILABLE, "1.1.4", None, None),
    (DeviceStatus.MAINTENANCE, "1.0.9", 4, 3),
    (DeviceStatus.RETIRED, "0.9.1", None, None),
]


# --------------------------------------------------------------------------- #
# Síntesis de ECG
# --------------------------------------------------------------------------- #


def _active_event(specs: list[EventSpec], t: float) -> EventSpec | None:
    for spec in specs:
        if spec.start_s <= t < spec.start_s + spec.duration_s:
            return spec
    return None


def _beat_times(
    rng: np.random.Generator, duration_s: float, base_bpm: float, specs: list[EventSpec]
) -> list[tuple[float, bool]]:
    """Instantes de cada QRS. El bool indica si el latido lleva onda P (no en AFIB)."""
    beats: list[tuple[float, bool]] = []
    t = 0.4 + float(rng.random()) * 0.4
    while t < duration_s:
        spec = _active_event(specs, t)
        bpm = base_bpm if spec is None or spec.bpm is None else spec.bpm
        has_p = True
        rr = 60.0 / bpm

        if spec is not None and spec.type is ECGEventType.AFIB:
            # FA: RR francamente irregular y sin onda P.
            has_p = False
            rr *= 0.6 + float(rng.random()) * 0.8
        elif spec is not None and spec.type is ECGEventType.PAUSE:
            # Pausa: se saltea la actividad ventricular durante todo el evento.
            t += spec.duration_s
            continue
        elif spec is not None and spec.type is ECGEventType.PVC:
            # Extrasístole: latido adelantado seguido de pausa compensadora.
            rr *= 0.55
        else:
            rr += (float(rng.random()) - 0.5) * 0.04

        beats.append((t, has_p))
        t += rr
    return beats


def _add_gaussian(
    signal: np.ndarray, fs: int, center_s: float, width_s: float, amplitude: float
) -> None:
    sigma = width_s / 4.0
    half = int(4 * sigma * fs) + 1
    center = int(center_s * fs)
    start = max(center - half, 0)
    end = min(center + half, signal.size)
    if start >= end:
        return
    t = (np.arange(start, end) / fs) - center_s
    signal[start:end] += amplitude * np.exp(-0.5 * (t / sigma) ** 2)


def synth_ecg(
    seed: str, duration_s: float, base_bpm: float, specs: list[EventSpec], fs: int = SAMPLE_RATE
) -> np.ndarray:
    """Señal de un canal en mV, muestreada a `fs`, con las arritmias de `specs`."""
    rng = np.random.default_rng(abs(hash(seed)) % (2**32))
    n = int(duration_s * fs)
    signal = np.zeros(n, dtype=np.float64)

    qrs_amp = 0.9 + float(rng.random()) * 0.2
    for center, has_p in _beat_times(rng, duration_s, base_bpm, specs):
        if has_p:
            _add_gaussian(signal, fs, center - 0.12, 0.08, qrs_amp * 0.15)
        _add_gaussian(signal, fs, center - 0.03, 0.03, -qrs_amp * 0.12)  # Q
        _add_gaussian(signal, fs, center, 0.05, qrs_amp)  # R
        _add_gaussian(signal, fs, center + 0.035, 0.035, -qrs_amp * 0.2)  # S
        _add_gaussian(signal, fs, center + 0.2, 0.16, qrs_amp * 0.3)  # T

    t = np.arange(n) / fs
    signal += 0.08 * np.sin(2 * np.pi * 0.3 * t)  # línea de base respiratoria
    signal += 0.02 * rng.standard_normal(n)  # ruido de fondo

    # Ruido de electrodo: solo dentro de los eventos NOISE.
    for spec in specs:
        if spec.type is not ECGEventType.NOISE:
            continue
        start = max(int(spec.start_s * fs), 0)
        end = min(int((spec.start_s + spec.duration_s) * fs), n)
        if start < end:
            signal[start:end] += 0.35 * rng.standard_normal(end - start)

    return signal.astype(np.float32)


# --------------------------------------------------------------------------- #
# S3 / MinIO
# --------------------------------------------------------------------------- #


def _s3_client() -> Any:
    return get_s3_client()


def _ensure_bucket(client: Any) -> None:
    ensure_bucket()


def _delete_keys(client: Any, keys: list[str]) -> None:
    delete_keys(keys)


def _put(client: Any, key: str, payload: bytes) -> None:
    put_object(key, payload)


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #


async def _get_or_create_doctor(db: AsyncSession, email: str) -> Doctor:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        raise SystemExit(
            f"El usuario {email} no existe o no está activo. "
            "Crealo previamente desde la administración de usuarios."
        )

    doctor = await db.scalar(select(Doctor).where(Doctor.user_id == user.id))
    if doctor is None:
        if user.role != UserRole.MEDICO:
            raise SystemExit(
                f"El admin {email} no tiene un perfil de médico al cual asignar los datos de demo. "
                "Usá --doctor-email con un médico activo."
            )
        doctor = Doctor(user_id=user.id, specialty="Cardiología", license_number="MN 12345")
        db.add(doctor)
        await db.flush()
        print(f"  · perfil de médico creado para {email}")
    return doctor


async def _reset(db: AsyncSession, client: Any) -> None:
    """Borra (hard delete) todo lo marcado como demo, respetando las FKs."""
    patient_ids = list(
        (
            await db.scalars(
                select(Patient.id).where(Patient.medical_record_num.startswith(MRN_PREFIX))
            )
        ).all()
    )
    device_ids = list(
        (
            await db.scalars(
                select(Device.id).where(Device.serial_number.startswith(SERIAL_PREFIX))
            )
        ).all()
    )
    batch_ids = list(
        (await db.scalars(select(ECGBatch.id).where(ECGBatch.device_id.in_(device_ids)))).all()
        if device_ids
        else []
    )

    # Los objetos de S3 se borran antes que las filas que los referencian.
    s3_keys = [
        key
        for key in (
            await db.scalars(
                select(Study.ecg_s3_key).where(
                    Study.patient_id.in_(patient_ids), Study.ecg_s3_key.is_not(None)
                )
            )
        ).all()
        if key is not None
    ]
    if batch_ids:
        s3_keys += list(
            (await db.scalars(select(ECGBatch.s3_key).where(ECGBatch.id.in_(batch_ids)))).all()
        )
    if s3_keys:
        _delete_keys(client, s3_keys)

    if patient_ids or batch_ids:
        await db.execute(
            delete(Alert).where(
                Alert.patient_id.in_(patient_ids)
                | Alert.event_id.in_(select(ECGEvent.id).where(ECGEvent.batch_id.in_(batch_ids)))
            )
        )
    if batch_ids:
        await db.execute(delete(ECGEvent).where(ECGEvent.batch_id.in_(batch_ids)))
        await db.execute(delete(ECGBatch).where(ECGBatch.id.in_(batch_ids)))
    if patient_ids:
        await db.execute(delete(Study).where(Study.patient_id.in_(patient_ids)))
        await db.execute(delete(Device).where(Device.patient_id.in_(patient_ids)))
        await db.execute(delete(Patient).where(Patient.id.in_(patient_ids)))
    if device_ids:
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
    await db.flush()
    print(
        f"  · borrados {len(patient_ids)} pacientes, {len(device_ids)} Holters "
        f"y {len(s3_keys)} objetos de S3 de demo"
    )


def _new_device(serial: str, status: DeviceStatus, firmware: str) -> Device:
    api_key = secrets.token_urlsafe(32)
    return Device(
        serial_number=serial,
        model="Holter ECG Austral",
        api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
        firmware_version=firmware,
        status=status,
    )


_ALERT_SEVERITY = {
    ECGEventSeverity.LOW: AlertSeverity.LOW,
    ECGEventSeverity.MEDIUM: AlertSeverity.MEDIUM,
    ECGEventSeverity.HIGH: AlertSeverity.HIGH,
    ECGEventSeverity.CRITICAL: AlertSeverity.CRITICAL,
}

_EVENT_LABEL = {
    ECGEventType.TACHYCARDIA: "Taquicardia sostenida",
    ECGEventType.BRADYCARDIA: "Bradicardia sostenida",
    ECGEventType.AFIB: "Fibrilación auricular",
    ECGEventType.PVC: "Extrasístole ventricular",
    ECGEventType.PAUSE: "Pausa ventricular",
    ECGEventType.NOISE: "Señal ruidosa",
    ECGEventType.OTHER: "Hallazgo",
}


async def _seed_study(
    db: AsyncSession,
    client: Any,
    patient: Patient,
    device: Device,
    spec: StudySpec,
    index: int,
    batch_minutes: float,
) -> Study:
    started_at = NOW - timedelta(hours=spec.starts_hours_ago)
    study = Study(
        patient_id=patient.id,
        device_id=device.id,
        started_at=started_at,
        status=spec.status,
        sample_rate=SAMPLE_RATE,
    )

    if spec.minutes <= 0:
        # Estudio agendado o cancelado: sin señal ni eventos.
        study.ended_at = None if spec.status is StudyStatus.SCHEDULED else started_at
        study.duration_ms = 0
        db.add(study)
        await db.flush()
        return study

    duration_s = spec.minutes * 60
    signal = synth_ecg(f"{patient.dni}-{index}", duration_s, spec.base_bpm, spec.events)

    study.samples_count = int(signal.size)
    study.duration_ms = int(signal.size / SAMPLE_RATE * 1000)
    study.ended_at = (
        None
        if spec.status is StudyStatus.IN_PROGRESS
        else started_at + timedelta(milliseconds=study.duration_ms)
    )
    study.events_count = len(spec.events)
    db.add(study)
    await db.flush()

    raw_signal = signal.astype("<f4", copy=False)
    raw_bytes = raw_signal.tobytes()
    study.ecg_s3_key = f"studies/{study.id}/ecg.f32"
    study.ecg_encoding = "float32-le"
    study.ecg_byte_length = len(raw_bytes)
    study.ecg_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    _put(client, study.ecg_s3_key, raw_bytes)
    pyramid_levels: list[dict[str, int | str]] = []
    for bucket_size in PYRAMID_BUCKETS:
        bucket_count = (raw_signal.size + bucket_size - 1) // bucket_size
        if bucket_count * 2 >= raw_signal.size:
            continue
        envelope = np.empty(bucket_count * 2, dtype="<f4")
        for bucket_index in range(bucket_count):
            bucket = raw_signal[
                bucket_index * bucket_size : min((bucket_index + 1) * bucket_size, raw_signal.size)
            ]
            envelope[bucket_index * 2] = bucket.min()
            envelope[bucket_index * 2 + 1] = bucket.max()
        level_bytes = envelope.tobytes()
        level_key = f"studies/{study.id}/ecg.minmax.{bucket_size}.f32"
        _put(client, level_key, level_bytes)
        pyramid_levels.append(
            {
                "key": level_key,
                "samplesPerBucket": bucket_size,
                "pointCount": int(envelope.size),
                "byteLength": len(level_bytes),
                "sha256": hashlib.sha256(level_bytes).hexdigest(),
            }
        )
    study.ecg_pyramid_levels = pyramid_levels

    # Un `ecg_batch` por cada tramo que el dispositivo habría subido.
    chunk = int(batch_minutes * 60 * SAMPLE_RATE)
    for offset in range(0, signal.size, chunk):
        piece = signal[offset : offset + chunk]
        chunk_start_s = offset / SAMPLE_RATE
        received_at = started_at + timedelta(seconds=chunk_start_s + piece.size / SAMPLE_RATE)
        key = f"batches/{device.serial_number}/{int(received_at.timestamp())}.f32"
        _put(client, key, piece.tobytes())

        batch = ECGBatch(
            device_id=device.id,
            received_at=received_at,
            batch_timestamp=int((started_at + timedelta(seconds=chunk_start_s)).timestamp()),
            duration_seconds=int(piece.size / SAMPLE_RATE),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            num_samples=int(piece.size),
            compression_type="raw-f32",
            s3_key=key,
            file_size_bytes=int(piece.nbytes),
            processing_status=ProcessingStatus.DONE,
            firmware_version=device.firmware_version,
        )
        db.add(batch)
        await db.flush()

        chunk_end_s = chunk_start_s + piece.size / SAMPLE_RATE
        for spec_event in spec.events:
            if not chunk_start_s <= spec_event.start_s < chunk_end_s:
                continue
            event = ECGEvent(
                batch_id=batch.id,
                event_type=spec_event.type,
                severity=spec_event.severity,
                timestamp_in_recording=spec_event.start_s - chunk_start_s,
                duration_seconds=spec_event.duration_s,
                confidence_score=round(0.72 + 0.27 * (spec_event.start_s % 1), 2),
                event_metadata={
                    "studyId": str(study.id),
                    "offsetInStudySeconds": spec_event.start_s,
                    "bpm": spec_event.bpm,
                },
            )
            db.add(event)
            await db.flush()

            if spec_event.severity in (ECGEventSeverity.LOW,):
                continue
            minutes = int(spec_event.start_s // 60)
            seconds = int(spec_event.start_s % 60)
            alert = Alert(
                patient_id=patient.id,
                event_id=event.id,
                severity=_ALERT_SEVERITY[spec_event.severity],
                message=(
                    f"{_EVENT_LABEL[spec_event.type]} detectada a los "
                    f"{minutes:02d}:{seconds:02d} del estudio "
                    f"({int(spec_event.duration_s)} s)."
                ),
                seen_at=None if spec_event.severity is ECGEventSeverity.CRITICAL else received_at,
            )
            db.add(alert)

    return study


async def _run(doctor_email: str, reset: bool, confirm_reset: bool, batch_minutes: float) -> None:
    if reset and settings.environment not in {Environment.DEVELOPMENT, Environment.TEST}:
        raise SystemExit("--reset está permitido únicamente en development/test.")
    if reset and not confirm_reset:
        raise SystemExit("--reset requiere también --confirm-reset.")

    client = _s3_client()
    _ensure_bucket(client)

    async with async_session_factory() as db:
        if reset:
            await _reset(db, client)

        existing = await db.scalar(
            select(Patient.id).where(Patient.medical_record_num.startswith(MRN_PREFIX)).limit(1)
        )
        if existing is not None:
            raise SystemExit("Ya hay datos de demo cargados. Corré con --reset para regenerarlos.")

        doctor = await _get_or_create_doctor(db, doctor_email)

        studies = 0
        devices = 0
        for i, spec in enumerate(PATIENTS, start=1):
            patient = Patient(
                doctor_id=doctor.id,
                medical_record_num=f"{MRN_PREFIX}{spec.dni}",
                first_name=spec.first_name,
                last_name=spec.last_name,
                date_of_birth=spec.birth_date,
                dni=spec.dni,
                sex=spec.sex,
                study_status=spec.study_status,
                phone=spec.phone,
                email=spec.email,
                notes=spec.notes,
            )
            db.add(patient)
            await db.flush()

            device: Device | None = None
            if spec.device:
                device = _new_device(
                    f"{SERIAL_PREFIX}{i:03d}", DeviceStatus.ASSIGNED, spec.firmware
                )
                device.patient_id = patient.id
                device.doctor_id = doctor.id
                device.last_battery_pct = spec.battery_pct
                device.last_sd_free_mb = spec.sd_free_mb
                device.last_seen_at = NOW - timedelta(minutes=7 * i)
                db.add(device)
                await db.flush()
                devices += 1

            if spec.studies and device is None:
                # Estudios pasados de un paciente sin Holter asignado hoy: se usa
                # un dispositivo devuelto al stock.
                device = _new_device(
                    f"{SERIAL_PREFIX}{i:03d}", DeviceStatus.AVAILABLE, spec.firmware
                )
                device.last_seen_at = NOW - timedelta(days=2 * i)
                device.doctor_id = doctor.id
                device.last_battery_pct = 100
                device.last_sd_free_mb = SD_TOTAL_MB
                db.add(device)
                await db.flush()
                devices += 1

            last_data: datetime | None = None
            for index, study_spec in enumerate(spec.studies):
                if device is None:
                    raise RuntimeError("A seeded study requires a device")
                study = await _seed_study(
                    db, client, patient, device, study_spec, index, batch_minutes
                )
                studies += 1
                if study_spec.minutes > 0:
                    end = study.ended_at or study.started_at + timedelta(
                        milliseconds=study.duration_ms or 0
                    )
                    last_data = max(last_data or end, end)

            patient.last_data_received_at = last_data

        for j, (status, firmware, battery, sd_free) in enumerate(
            SPARE_DEVICES, start=len(PATIENTS) + 1
        ):
            device = _new_device(f"{SERIAL_PREFIX}{j:03d}", status, firmware)
            device.last_battery_pct = battery
            device.last_sd_free_mb = sd_free
            device.last_seen_at = None if battery is None else NOW - timedelta(days=j)
            db.add(device)
            devices += 1

        await db.commit()

    print(
        f"✓ Seed lista: {len(PATIENTS)} pacientes, {devices} Holters, {studies} estudios "
        f"(médico: {doctor_email})."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga datos de demo en la base.")
    parser.add_argument(
        "--doctor-email",
        default="dev@tesis.com",
        help="Médico dueño de los pacientes de demo (default: dev@tesis.com).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Borra los datos de demo previos antes de cargar.",
    )
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="Confirmación explícita requerida junto con --reset.",
    )
    parser.add_argument(
        "--batch-minutes",
        type=float,
        default=5,
        help="Minutos de señal por ecg_batch (default: 5).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.doctor_email, args.reset, args.confirm_reset, args.batch_minutes))


if __name__ == "__main__":
    main()
