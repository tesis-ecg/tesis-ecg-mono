"""Regressions at the device/ACK and visualization boundaries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np
from fastapi import BackgroundTasks
from sqlalchemy import select

from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.study import Study, StudyStatus
from app.main import _is_origin_exempt
from app.modules.devices.devices_repository import assign_device, unassign_device
from app.modules.devices.devices_schemas import HolterIdInput
from app.modules.devices.devices_service import unassign_holter
from app.modules.ingest.processing import _human_duration, append_filtered_view, process_batch
from app.modules.ingest.visual_filter import filter_visualization
from tests.ingest_helpers import build_frames, post_frames


async def test_changed_bytes_with_same_boot_open_new_study(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    original = build_frames(1800, boot_id=2)
    first = (await post_frames(client, device, key, original, boot_id=2)).json()
    duplicate = (await post_frames(client, device, key, original, boot_id=2)).json()
    assert duplicate["framesDuplicate"] == len(original)
    assert duplicate["studyId"] == first["studyId"]

    changed = build_frames(1800, boot_id=2, t0_ms=100)
    second = (await post_frames(client, device, key, changed, boot_id=2)).json()
    assert second["studyId"] != first["studyId"]
    assert second["framesAccepted"] == len(changed)
    assert second["framesDuplicate"] == 0
    previous = await db.get(Study, first["studyId"])
    assert previous is not None and previous.status is StudyStatus.COMPLETED


async def test_rewind_closes_a_future_dated_study_without_a_500(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    original = build_frames(900)
    first = (await post_frames(client, device, key, original)).json()
    previous = await db.get(Study, first["studyId"])
    assert previous is not None
    previous.started_at = datetime.now(UTC) + timedelta(days=1)
    await db.commit()

    response = await post_frames(client, device, key, build_frames(900, t0_ms=1000))
    assert response.status_code == 202
    assert response.json()["studyId"] != first["studyId"]
    await db.refresh(previous)
    assert previous.ended_at is not None and previous.ended_at >= previous.started_at


async def test_a_foreign_boot_anchor_is_archived_but_not_verified(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    body = (await post_frames(client, device, key, build_frames(900, boot_id=3), boot_id=4)).json()
    study = await db.get(Study, body["studyId"])
    batch = await db.get(ECGBatch, body["batchId"])
    assert study is not None and batch is not None
    assert study.started_at_verified is False
    assert batch.anchor_matches_boot is False
    assert batch.time_sync_uncertainty_ms is None


async def test_a_legacy_anchor_cannot_start_a_study_after_its_upload(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    body = (
        await post_frames(
            client,
            device,
            key,
            build_frames(900, t0_ms=3_600_000),
            uptime_ms=1_000,
        )
    ).json()
    study = await db.get(Study, body["studyId"])
    batch = await db.get(ECGBatch, body["batchId"])
    assert study is not None and batch is not None
    assert study.started_at <= batch.received_at
    assert study.started_at_verified is False
    assert batch.anchor_matches_boot is False


async def test_reassignment_rearms_battery_alert_for_the_next_patient(
    client, s3, db, make_patient, make_device
) -> None:
    first_patient = await make_patient()
    second_patient = await make_patient()
    device, key = await make_device(patient=first_patient)
    frames = build_frames(900)
    await post_frames(client, device, key, frames, battery_flags=0x03)
    await db.refresh(device)
    assert device.battery_alert_level == "low"
    await unassign_device(db, device)
    await assign_device(db, device, second_patient)
    await db.commit()
    await post_frames(client, device, key, frames, battery_flags=0x03)
    alerts = list((await db.scalars(select(Alert).where(Alert.kind == "battery_low"))).all())
    assert {alert.patient_id for alert in alerts} == {first_patient.id, second_patient.id}


async def test_battery_alerts_are_per_episode_and_telemetry_is_saved(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    frames = build_frames(900)
    for flags in (0x03, 0x03, 0x07, 0x07, 0x01, 0x03):
        response = await post_frames(
            client, device, key, frames, battery_flags=flags, rssi=-62, sqi=2
        )
        assert response.status_code == 202
    await db.refresh(device)
    assert device.last_rssi_dbm == -62
    assert device.last_sqi == 2
    assert device.last_battery_flags == 0x03
    alerts = list((await db.scalars(select(Alert).where(Alert.patient_id == patient.id))).all())
    battery_kinds = [
        alert.kind for alert in alerts if alert.kind and alert.kind.startswith("battery_")
    ]
    assert battery_kinds == ["battery_low", "battery_critical", "battery_low"]


async def test_filtered_tail_waits_and_then_completes_without_duplicate_segments(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    frames = build_frames(5000)
    first = (await post_frames(client, device, key, frames[: len(frames) // 2])).json()
    with patch("app.modules.ingest.processing.CONTEXT_SECONDS", 1):
        await process_batch(db, first["batchId"])
    study = await db.get(Study, first["studyId"])
    assert study is not None
    first_count = study.samples_count
    assert study.filtered_samples_count == first_count - 500

    second = (await post_frames(client, device, key, frames[len(frames) // 2 :])).json()
    with patch("app.modules.ingest.processing.CONTEXT_SECONDS", 1):
        await process_batch(db, second["batchId"])
    assert study.filtered_samples_count == study.samples_count - 500
    study.status = StudyStatus.COMPLETED
    study.ended_at = max(datetime.now(UTC), study.started_at)
    await append_filtered_view(db, study)
    await append_filtered_view(db, study)
    assert study.filtered_samples_count == study.samples_count
    covered = sum(int(part["sampleCount"]) for part in study.ecg_filtered_segments)
    assert covered == study.samples_count
    assert len({part["key"] for part in study.ecg_filtered_segments}) == len(
        study.ecg_filtered_segments
    )


async def test_unassign_schedules_the_filtered_tail(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    body = (await post_frames(client, device, key, build_frames(900), boot_id=0)).json()
    await process_batch(db, body["batchId"])
    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.filtered_samples_count == 0

    background = BackgroundTasks()
    await unassign_holter(HolterIdInput(doctor_id=None, device_id=device.id), db, background)

    assert study.status is StudyStatus.COMPLETED
    assert len(background.tasks) == 1
    assert background.tasks[0].args == (study.id,)


async def test_failed_and_pending_batches_recover_in_order_without_duplicate_segments(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    frames = build_frames(1800)
    split = len(frames) // 2
    first = (await post_frames(client, device, key, frames[:split], boot_id=0)).json()
    second = (await post_frames(client, device, key, frames[split:], boot_id=0)).json()
    failed = await db.get(ECGBatch, first["batchId"])
    assert failed is not None
    failed.processing_status = ProcessingStatus.FAILED
    failed.processing_error = "fallo transitorio"
    await db.flush()

    await process_batch(db, second["batchId"])
    await process_batch(db, second["batchId"])

    study = await db.get(Study, first["studyId"])
    assert study is not None
    assert failed.processing_status is ProcessingStatus.DONE
    assert study.samples_count == 1800
    assert sum(int(part["sampleCount"]) for part in study.ecg_segments) == 1800
    assert len({part["key"] for part in study.ecg_segments}) == len(study.ecg_segments)


def test_visual_filter_rejects_mains_and_has_no_phase_shift() -> None:
    fs = 500
    t = np.arange(fs * 180) / fs
    clean = np.sin(2 * np.pi * 5 * t)
    mains = 0.7 * np.sin(2 * np.pi * 50 * t)
    result = filter_visualization((clean + mains).astype("<f4"), fs)
    middle = slice(fs * 60, fs * 120)
    assert np.sqrt(np.mean((result[middle] - clean[middle]) ** 2)) < 0.02
    assert np.argmax(result[fs * 90 : fs * 90 + 100]) == np.argmax(clean[fs * 90 : fs * 90 + 100])


def test_short_loss_does_not_render_zero_minutes() -> None:
    assert _human_duration(10_000) == "10 s"
    assert _human_duration(0) == "no determinada"


def test_visual_filter_is_continuous_across_blocks_with_context() -> None:
    fs = 500
    t = np.arange(fs * 600) / fs
    raw = (
        np.sin(2 * np.pi * t) + 0.2 * np.sin(2 * np.pi * 0.2 * t) + 0.3 * np.sin(2 * np.pi * 50 * t)
    ).astype("<f4")
    whole = filter_visualization(raw, fs)
    boundary = 300 * fs
    context = 120 * fs
    left = filter_visualization(raw[: boundary + context], fs)[:boundary]
    right = filter_visualization(raw[boundary - context :], fs)[context:]
    stitched = np.concatenate((left, right))
    error = stitched[boundary - fs : boundary + fs] - whole[boundary - fs : boundary + fs]
    assert np.max(np.abs(error)) < 0.002


def test_origin_exemption_matches_only_the_deployed_bearer_prefixes() -> None:
    assert _is_origin_exempt("/api/ingest/ecg-frames")
    assert _is_origin_exempt("/api/mobile/reports")
    assert not _is_origin_exempt("/api/ingestion/anything")
    assert not _is_origin_exempt("/api/studies/anything")
