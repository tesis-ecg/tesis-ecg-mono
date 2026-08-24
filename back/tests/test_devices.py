import uuid
from datetime import UTC, datetime

from app.db.models.device import Device, DeviceStatus
from app.modules.devices.devices_service import holter_health_out


def test_holter_health_does_not_invent_missing_telemetry() -> None:
    device = Device(
        id=uuid.uuid4(),
        serial_number="TEST-001",
        model="Holter ECG",
        api_key_hash="hash",
        status=DeviceStatus.AVAILABLE,
        last_seen_at=datetime.now(UTC),
        last_battery_pct=None,
        last_sd_free_mb=None,
    )

    health = holter_health_out(device)

    assert health.batteryPercent is None
    assert health.signalDbm is None
    assert health.nextScheduledUploadAt is None
    assert health.uploadsToday is None
    assert health.storageTotalMb is None
    assert health.telemetryAvailable is False
    assert health.signalQuality is None


# --------------------------------------------------------------------------- #
# Campos derivados: nombres en vez de UUIDs
# --------------------------------------------------------------------------- #


async def test_the_list_carries_the_patient_name_not_just_the_id(
    db, as_user, make_user, make_patient, make_device
) -> None:
    """La grilla mostraba el UUID del paciente porque el DTO no traía otra cosa."""
    from app.db.models.user import UserRole

    patient = await make_patient(first_name="Ana", last_name="Pérez")
    device, _ = await make_device(patient=patient)
    await db.commit()

    response = await as_user(await make_user(UserRole.ADMIN)).get("/devices")

    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["id"] == str(device.id))
    assert item["assignedPatientId"] == str(patient.id)
    assert item["assignedPatientName"] == "Ana Pérez"


async def test_the_detail_links_the_study_in_progress(
    db, as_user, make_user, make_patient, make_device, make_study
) -> None:
    from app.db.models.user import UserRole

    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await make_user(UserRole.ADMIN)).get(f"/devices/{device.id}")

    assert response.status_code == 200
    assert response.json()["activeStudyId"] == str(study.id)


async def test_a_device_without_an_open_study_has_no_active_study_id(
    db, as_user, make_user, make_patient, make_device, make_study
) -> None:
    from app.db.models.study import StudyStatus
    from app.db.models.user import UserRole

    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    await make_study(patient, device, status=StudyStatus.COMPLETED)
    await db.commit()

    response = await as_user(await make_user(UserRole.ADMIN)).get(f"/devices/{device.id}")

    assert response.json()["activeStudyId"] is None


async def test_the_patient_list_carries_the_device_serial(
    db, as_user, make_user, make_patient, make_device
) -> None:
    from app.db.models.user import UserRole

    patient = await make_patient()
    device, _ = await make_device(patient=patient, serial_number="HOL-CONTRACT-1")
    await db.commit()

    response = await as_user(await make_user(UserRole.ADMIN)).get("/patients")

    item = next(i for i in response.json()["items"] if i["id"] == str(patient.id))
    assert item["assignedDeviceId"] == str(device.id)
    assert item["assignedDeviceSerial"] == "HOL-CONTRACT-1"
