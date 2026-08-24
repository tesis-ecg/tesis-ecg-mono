"""Dos lotes al mismo tiempo.

Un chaleco reintentando mientras el request original sigue en vuelo es normal
(el equipo no espera indefinidamente). Si el cursor de `seq` se lee sin bloqueo,
los dos requests ven el mismo valor viejo y uno de los dos lotes se pierde o se
duplica — en silencio, porque los dos responden 202.

Estos tests usan conexiones reales: el fixture `db` corre todo sobre una sola
conexión con rollback, donde nada puede competir de verdad.
"""

import asyncio
import hashlib
import secrets
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.db.models.user import IdentityStatus, User, UserRole
from app.dependencies.device_dependencies import DeviceContext
from app.modules.devices import devices_service
from app.modules.devices.devices_schemas import AssignHolterInput
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest.ingest_schemas import IngestFramesInput
from app.modules.ingest.ingest_service import ingest_frames
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames


async def _seed_device(world, session) -> tuple[Device, Patient]:
    suffix = uuid.uuid4().hex[:10]
    user = User(
        auth0_id=f"auth0|{suffix}",
        email=f"{suffix}@example.test",
        full_name=f"Doctor {suffix}",
        role=UserRole.MEDICO,
        is_active=True,
        identity_status=IdentityStatus.ACTIVE,
        session_version=1,
    )
    session.add(user)
    await session.flush()
    doctor = Doctor(user_id=user.id, specialty="Cardiología", license_number=f"MN-{suffix}")
    session.add(doctor)
    await session.flush()
    world.track(doctor)

    patient = Patient(
        doctor_id=doctor.id,
        medical_record_num=f"HC-{suffix}",
        first_name="Paciente",
        last_name=suffix,
        date_of_birth=datetime(1970, 1, 1).date(),
        dni=str(30_000_000 + int(suffix[:6], 16) % 9_000_000),
        sex=PatientSex.M,
        study_status=PatientStudyStatus.ACTIVE,
    )
    session.add(patient)
    await session.flush()

    device = Device(
        serial_number=f"HOL-{suffix.upper()}",
        model="Holter ECG",
        api_key_hash=hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        status=DeviceStatus.ASSIGNED,
        firmware_version="1.0.0",
    )
    session.add(device)
    await session.commit()
    return device, patient


async def _ingest_with_own_session(world, device_id: uuid.UUID, frames: list[bytes]) -> dict:
    async with world.session() as session:
        device = await session.get(Device, device_id)
        assert device is not None
        ack = await ingest_frames(
            DeviceContext(
                device=device, uptime_ms=3_600_000, firmware_version="1.0.0", battery_pct=90
            ),
            IngestFramesInput(payload=b"".join(frames), received_at=datetime.now(UTC)),
            session,
            background=None,
        )
        return ack.model_dump()


@pytest.fixture
async def seeded(committed_world, s3):
    async with committed_world.session() as session:
        device, patient = await _seed_device(committed_world, session)
    return committed_world, device, patient


async def test_the_open_study_query_actually_takes_a_row_lock(seeded) -> None:
    """Prueba el mecanismo, no el síntoma.

    Un test end-to-end con `asyncio.gather` casi nunca cae en la ventana de la
    carrera: las queries son rápidas y las corrutinas se serializan solas. Este
    verifica lo que importa de forma determinista — que la segunda sesión se
    **bloquea** mientras la primera tiene la fila tomada. Si alguien saca el
    `with_for_update()`, esto falla siempre, no una vez cada tanto.
    """
    world, device, patient = seeded
    async with world.session() as setup:
        study = Study(
            patient_id=patient.id,
            device_id=device.id,
            started_at=datetime.now(UTC),
            status=StudyStatus.IN_PROGRESS,
            sample_rate=500,
        )
        setup.add(study)
        await setup.commit()

    holder = world.session()
    await holder.begin()
    locked = await repo.get_open_study_for_update(holder, patient.id, device.id)
    assert locked is not None
    locked_id = locked.id

    waiter = world.session()
    await waiter.begin()
    pending = asyncio.create_task(repo.get_open_study_for_update(waiter, patient.id, device.id))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), timeout=1.0)

        await holder.rollback()
        await holder.close()

        second = await asyncio.wait_for(pending, timeout=5.0)
        assert second is not None
        assert second.id == locked_id
    finally:
        pending.cancel()
        await waiter.rollback()
        await waiter.close()
        await holder.close()


async def test_the_device_lock_serializes_even_when_no_study_exists(seeded) -> None:
    """El primer estudio no tiene fila propia para bloquear: se usa el equipo."""
    world, device, _ = seeded
    holder = world.session()
    await holder.begin()
    locked = await repo.get_device_for_update(holder, device.id)
    assert locked is not None

    waiter = world.session()
    await waiter.begin()
    pending = asyncio.create_task(repo.get_device_for_update(waiter, device.id))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), timeout=1.0)

        await holder.rollback()
        second = await asyncio.wait_for(pending, timeout=5.0)
        assert second is not None
        assert second.id == device.id
    finally:
        pending.cancel()
        await waiter.rollback()
        await waiter.close()
        await holder.close()


async def test_ingest_revalidates_a_device_that_changed_after_authentication(seeded) -> None:
    world, stale_device, _ = seeded
    async with world.session() as updater:
        current = await updater.get(Device, stale_device.id)
        assert current is not None
        current.patient_id = None
        current.doctor_id = None
        current.status = DeviceStatus.MAINTENANCE
        await updater.commit()

    async with world.session() as session:
        with pytest.raises(HTTPException) as caught:
            await ingest_frames(
                DeviceContext(
                    device=stale_device,
                    uptime_ms=3_600_000,
                    firmware_version="1.0.0",
                    battery_pct=90,
                ),
                IngestFramesInput(
                    payload=b"".join(build_frames(900)),
                    received_at=datetime.now(UTC),
                ),
                session,
                background=None,
            )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "DEVICE_NOT_INGESTABLE"


async def test_two_concurrent_batches_never_overlap_seq_ranges(seeded) -> None:
    """Ningún rango de `seq` puede aparecer en dos lotes distintos.

    Un solapamiento significaría la misma señal guardada dos veces; un hueco no
    cubierto por nadie, señal del paciente perdida con el equipo creyendo que la
    entregó.
    """
    world, device, _ = seeded
    frames = build_frames(2400)
    half = len(frames) // 2

    first, second = await asyncio.gather(
        _ingest_with_own_session(world, device.id, frames[:half]),
        _ingest_with_own_session(world, device.id, frames[half:]),
    )

    async with world.session() as session:
        studies = list((await session.scalars(select(Study))).all())
        batches = list((await session.scalars(select(ECGBatch))).all())

    assert first["studyId"] == second["studyId"]
    assert len(studies) == 1

    covered: set[int] = set()
    for batch in batches:
        assert batch.first_seq is not None and batch.last_seq is not None
        span = set(range(batch.first_seq, batch.last_seq + 1))
        assert not (span & covered), "dos lotes cubren la misma seq"
        covered |= span

    study = studies[0]
    if batches:
        assert study.last_ingested_seq == max(b.last_seq or -1 for b in batches)


async def test_a_race_to_create_the_study_produces_only_one(seeded) -> None:
    """Dos lotes simultáneos sobre un equipo sin estudio abierto."""
    world, device, _ = seeded
    frames = build_frames(1200)

    await asyncio.gather(
        _ingest_with_own_session(world, device.id, frames[:2]),
        _ingest_with_own_session(world, device.id, frames[2:]),
    )

    async with world.session() as session:
        count = await session.scalar(select(func.count()).select_from(Study))
    assert count == 1


async def test_two_workers_process_the_same_batch_exactly_once(seeded) -> None:
    world, device, _ = seeded
    ack = await _ingest_with_own_session(world, device.id, build_frames(1500))
    assert ack["batchId"] is not None

    async def worker() -> None:
        async with world.session() as session:
            await process_batch(session, ack["batchId"])

    await asyncio.gather(worker(), worker())

    async with world.session() as session:
        study = await session.get(Study, ack["studyId"])
        batch = await session.get(ECGBatch, ack["batchId"])
    assert study is not None and batch is not None
    assert batch.processing_status is ProcessingStatus.DONE
    assert len(study.ecg_segments) == 1
    assert study.samples_count == batch.num_samples


async def test_reassignment_waits_for_ingest_and_never_moves_signal_between_patients(
    seeded,
) -> None:
    world, device, old_patient = seeded
    suffix = uuid.uuid4().hex[:10]
    async with world.session() as setup:
        new_patient = Patient(
            doctor_id=old_patient.doctor_id,
            medical_record_num=f"HC-DEST-{suffix}",
            first_name="Destino",
            last_name=suffix,
            date_of_birth=datetime(1975, 1, 1).date(),
            dni=str(40_000_000 + int(suffix[:6], 16) % 9_000_000),
            sex=PatientSex.F,
            study_status=PatientStudyStatus.NONE,
        )
        setup.add(new_patient)
        await setup.commit()
        new_patient_id = new_patient.id

    ingest_session = world.session()
    await ingest_session.begin()
    locked = await repo.get_device_for_update(ingest_session, device.id)
    assert locked is not None

    async def reassign() -> None:
        async with world.session() as session:
            await devices_service.reassign_holter(
                AssignHolterInput(
                    doctor_id=None,
                    device_id=device.id,
                    patient_id=new_patient_id,
                ),
                session,
            )

    moving = asyncio.create_task(reassign())
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(moving), timeout=1.0)

        ack = await ingest_frames(
            DeviceContext(
                device=locked,
                uptime_ms=3_600_000,
                firmware_version="1.0.0",
                battery_pct=90,
            ),
            IngestFramesInput(
                payload=b"".join(build_frames(1500)),
                received_at=datetime.now(UTC),
            ),
            ingest_session,
            background=None,
        )
        await asyncio.wait_for(moving, timeout=5.0)
    finally:
        moving.cancel()
        await ingest_session.close()

    async with world.session() as session:
        study = await session.get(Study, ack.studyId)
        moved_device = await session.get(Device, device.id)
    assert study is not None and moved_device is not None
    assert study.patient_id == old_patient.id
    assert study.status is StudyStatus.COMPLETED
    assert moved_device.patient_id == new_patient_id


async def test_devices_of_different_patients_do_not_block_each_other(committed_world, s3) -> None:
    """El bloqueo es por fila, no por tabla: dos equipos no se frenan entre sí."""
    async with committed_world.session() as session:
        device_a, _ = await _seed_device(committed_world, session)
    async with committed_world.session() as session:
        device_b, _ = await _seed_device(committed_world, session)

    results = await asyncio.gather(
        _ingest_with_own_session(committed_world, device_a.id, build_frames(1200)),
        _ingest_with_own_session(committed_world, device_b.id, build_frames(1200)),
    )

    assert results[0]["studyId"] != results[1]["studyId"]
    assert all(r["framesAccepted"] > 0 for r in results)
