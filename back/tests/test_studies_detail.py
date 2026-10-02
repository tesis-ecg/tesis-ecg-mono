"""Contrato enriquecido del detalle/listado de estudios."""

from datetime import UTC, datetime, timedelta

from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.user import User, UserRole


async def _doctor_user(db, doctor) -> User:  # type: ignore[no-untyped-def]
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


def _batch(device_id, study_id, received_at: datetime, suffix: str) -> ECGBatch:  # type: ignore[no-untyped-def]
    return ECGBatch(
        device_id=device_id,
        study_id=study_id,
        received_at=received_at,
        batch_timestamp=0,
        duration_seconds=1,
        sample_rate=500,
        num_channels=1,
        num_samples=500,
        compression_type="rice",
        s3_key=f"tests/{suffix}.bin",
        processing_status=ProcessingStatus.DONE,
    )


async def test_study_contract_exposes_device_and_latest_batch_from_that_study(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)

    other_patient = await make_patient(doctor=doctor)
    other_device, _ = await make_device(patient=other_patient)
    other_study = await make_study(other_patient, other_device)
    first = datetime.now(UTC) - timedelta(minutes=10)
    latest = datetime.now(UTC) - timedelta(minutes=5)
    unrelated = datetime.now(UTC)
    db.add_all(
        [
            _batch(device.id, study.id, first, "first"),
            _batch(device.id, study.id, latest, "latest"),
            _batch(other_device.id, other_study.id, unrelated, "unrelated"),
        ]
    )
    await db.commit()

    client = as_user(await _doctor_user(db, doctor))
    detail = await client.get(f"/studies/{study.id}")
    listing = await client.get("/studies")

    assert detail.status_code == 200
    assert detail.json()["deviceId"] == str(device.id)
    assert detail.json()["canAccessDevice"] is True
    assert datetime.fromisoformat(detail.json()["lastDataReceivedAt"]) == latest
    item = next(row for row in listing.json()["items"] if row["id"] == str(study.id))
    assert item["deviceId"] == str(device.id)
    assert item["canAccessDevice"] is True
    assert datetime.fromisoformat(item["lastDataReceivedAt"]) == latest


async def test_study_without_batches_has_no_last_data_received_at(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).get(f"/studies/{study.id}")

    assert response.status_code == 200
    assert response.json()["lastDataReceivedAt"] is None


async def test_historical_study_hides_a_device_transferred_to_another_doctor(
    db, as_user, make_user, make_doctor, make_patient, make_device, make_study
) -> None:
    original_doctor = await make_doctor()
    next_doctor = await make_doctor()
    patient = await make_patient(doctor=original_doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    original_user = await _doctor_user(db, original_doctor)
    client = as_user(original_user)
    unassigned = await client.post(f"/devices/{device.id}/unassign")
    assert unassigned.status_code == 200

    admin = await make_user(role=UserRole.ADMIN)
    client = as_user(admin)
    transferred = await client.post(
        f"/devices/{device.id}/assign-doctor", json={"doctorId": str(next_doctor.id)}
    )
    assert transferred.status_code == 200

    client = as_user(original_user)
    detail = await client.get(f"/studies/{study.id}")
    listing = await client.get("/studies")

    assert detail.status_code == 200
    assert detail.json()["canAccessDevice"] is False
    item = next(row for row in listing.json()["items"] if row["id"] == str(study.id))
    assert item["canAccessDevice"] is False

    client = as_user(admin)
    admin_detail = await client.get(f"/studies/{study.id}")
    assert admin_detail.status_code == 200
    assert admin_detail.json()["canAccessDevice"] is True
