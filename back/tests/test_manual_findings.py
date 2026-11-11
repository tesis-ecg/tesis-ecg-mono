"""Creación manual, coordenadas y efecto reversible sobre las métricas."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.alert import Alert
from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent
from app.db.models.study import Study, StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment
from app.modules.studies.corrections import doctor_corrections, is_reclassifiable
from app.modules.studies.manual_findings_service import manual_sample_span
from tests.test_studies_reclassification import _study, _user_of


@pytest.fixture
async def world(db, s3, as_user, make_doctor, make_patient, make_device, make_study):
    doctor, study, batch, _ = await _study(db, make_doctor, make_patient, make_device, make_study)
    batch.first_seq = 0
    study.ecg_segments = [{**study.ecg_segments[0], "firstSeq": 0}]
    await db.commit()
    user = await _user_of(db, doctor)
    return as_user(user), study, batch, user


def payload(study, **changes):
    epoch = int(study.started_at.timestamp() * 1000)
    return {
        "startEpochMs": epoch + 1000,
        "endEpochMs": epoch + 5000,
        "kind": "noise",
        "severity": "high",
        "clientRequestId": str(uuid.uuid4()),
        **changes,
    }


async def test_create_retry_reclassify_and_discard(world, db):
    client, study, batch, user = world
    url = f"/studies/{study.id}/findings"
    before = (await client.get(f"/studies/{study.id}/holter-metrics")).json()
    body = payload(study)
    response = await client.post(url, json=body)
    assert response.status_code == 201, response.text
    result = response.json()
    finding = result["finding"]
    assert finding["source"] == "manual"
    assert finding["createdBy"] == str(user.id)
    assert finding["createdByName"] == user.full_name
    assert finding["createdAt"]
    assert finding["reclassifiable"]
    assert finding["modelVersion"] is None
    assert result["holterMetrics"]["analysis"]["excludedMs"] > before["analysis"]["excludedMs"]
    retry = await client.post(url, json=body)
    assert retry.status_code == 201
    assert retry.json()["finding"]["id"] == finding["id"]
    event = await db.get(ECGEvent, uuid.UUID(finding["id"]))
    assert event.batch_id == batch.id
    assert event.event_metadata["startSampleIndex"] == 500
    assert event.event_metadata["sampleCount"] == 2000
    assert is_reclassifiable(event)
    assert event.model_version is None  # The engine's rewrite predicate excludes it.
    audits = (
        await db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.ECG_EVENT_CREATED,
                AuditEvent.event_metadata["event_id"].astext == finding["id"],
            )
        )
    ).all()
    assert len(audits) == 1
    assert not (await db.scalars(select(Alert).where(Alert.event_id == event.id))).all()
    findings = (await client.get(url)).json()
    assert any(item["id"] == finding["id"] for item in findings["ungrouped"])
    manifest = (await client.get(f"/studies/{study.id}/ecg/manifest")).json()
    annotation = next(item for item in manifest["annotations"] if item["id"] == finding["id"])
    assert annotation["source"] == "manual"
    assert annotation["startEpochMs"] == body["startEpochMs"]
    correction_url = f"{url}/{finding['id']}/reclassification"
    relabeled = await client.post(
        correction_url, json={"action": "relabel", "kind": "pvc", "severity": "critical"}
    )
    assert relabeled.status_code == 200, relabeled.text
    assert relabeled.json()["finding"]["correction"]["effectiveKind"] == "pvc"
    rejected = await client.post(correction_url, json={"action": "reject"})
    assert rejected.status_code == 200, rejected.text
    assert (
        rejected.json()["holterMetrics"]["analysis"]["excludedMs"]
        == before["analysis"]["excludedMs"]
    )
    await db.refresh(event)
    assert not doctor_corrections([event]).relabels
    assert not doctor_corrections([event]).noise_spans


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "unknown"},
        {"severity": "urgent"},
        {"severity": None},
        {"clientRequestId": "invalid"},
        {"startEpochMs": True},
        {"endEpochMs": 0},
        {"startEpochMs": 0},
    ],
)
async def test_invalid_request(world, change):
    client, study, _, _ = world
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study, **change))
    assert response.status_code == 422, response.text


async def test_same_key_with_different_body_is_rejected(world):
    client, study, _, _ = world
    body = payload(study)
    url = f"/studies/{study.id}/findings"
    assert (await client.post(url, json=body)).status_code == 201
    response = await client.post(url, json={**body, "severity": "low"})
    assert response.status_code == 422


async def test_other_doctor_cannot_create(world, make_doctor, db, as_user):
    _, study, _, _ = world
    client = as_user(await _user_of(db, await make_doctor()))
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study))
    assert response.status_code == 404


async def test_commit_survives_metrics_failure(world, monkeypatch, db):
    client, study, _, _ = world
    from app.modules.studies import reclassification_service

    monkeypatch.setattr(
        reclassification_service.studies_service,
        "get_holter_metrics",
        AsyncMock(side_effect=RuntimeError("S3")),
    )
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study))
    assert response.status_code == 201
    assert response.json()["holterMetrics"]["unavailableReason"] == "RECALCULATION_FAILED"
    assert await db.get(ECGEvent, uuid.UUID(response.json()["finding"]["id"])) is not None


async def test_cross_batch_range_and_cancelled_study(world, db):
    client, study, batch, _ = world
    second = ECGBatch(
        device_id=batch.device_id,
        study_id=study.id,
        received_at=batch.received_at,
        batch_timestamp=1,
        duration_seconds=4,
        sample_rate=500,
        num_channels=1,
        num_samples=1000,
        compression_type="raw-f32",
        s3_key="second",
        first_seq=1,
    )
    db.add(second)
    old = study.ecg_segments[0]
    study.ecg_segments = [
        {**old, "sampleCount": 1000},
        {**old, "startSampleIndex": 1000, "sampleCount": study.samples_count - 1000, "firstSeq": 1},
    ]
    await db.commit()
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study))
    assert response.status_code == 201, response.text
    event = await db.get(ECGEvent, uuid.UUID(response.json()["finding"]["id"]))
    assert event.batch_id == batch.id
    study.status = StudyStatus.CANCELLED
    await db.commit()
    assert (
        await client.post(f"/studies/{study.id}/findings", json=payload(study))
    ).status_code == 422


async def test_preview_changes_after_creation(world):
    client, study, _, _ = world
    url = f"/studies/{study.id}/clinical-report/preview"
    before = (await client.get(url)).json()
    assert (
        await client.post(f"/studies/{study.id}/findings", json=payload(study))
    ).status_code == 201
    after = (await client.get(url)).json()
    assert before["snapshotHash"] != after["snapshotHash"]


def test_timeline_inverse_accepts_unverified_but_rejects_gaps():
    study = Study(started_at=datetime(2026, 1, 1, tzinfo=UTC), samples_count=1000, sample_rate=500)
    runs = [
        StudyTimelineSegment(
            start_sample_index=0,
            sample_count=500,
            start_epoch_ms=10000,
            end_epoch_ms=11000,
            anchor_matches_boot=False,
        ),
        StudyTimelineSegment(
            start_sample_index=500,
            sample_count=500,
            start_epoch_ms=15000,
            end_epoch_ms=16000,
            anchor_matches_boot=False,
        ),
    ]
    assert manual_sample_span(study, runs, 10000, 11000) == (0, 500)
    assert manual_sample_span(study, runs, 15000, 16000) == (500, 1000)
    for start, end in [
        (10500, 15500),
        (12000, 13000),
        (9999, 10500),
        (16000, 16001),
        (10001, 10001),
    ]:
        with pytest.raises(HTTPException):
            manual_sample_span(study, runs, start, end)


async def test_manual_overlay_releases_existing_detector_pause(world, db):
    from tests.test_studies_reclassification import _pause_finding

    client, study, batch, _ = world
    # The synthetic trace's pause starts at 160 seconds.
    import numpy as np

    from tests.synthetic_ecg import beat_times

    rr = np.full(450, 0.8)
    rr[200] = 2.6
    original = await _pause_finding(db, batch, study, beat_times(rr))
    before = (await client.get(f"/studies/{study.id}/holter-metrics")).json()
    epoch = int(study.started_at.timestamp() * 1000)
    body = payload(study, startEpochMs=epoch + 159000, endEpochMs=epoch + 165000)
    result = await client.post(f"/studies/{study.id}/findings", json=body)
    assert result.status_code == 201, result.text
    manual_id = result.json()["finding"]["id"]
    assert result.json()["holterMetrics"]["pauses"]["count"] == 0
    discarded = await client.post(
        f"/studies/{study.id}/findings/{manual_id}/reclassification", json={"action": "reject"}
    )
    assert discarded.status_code == 200
    assert discarded.json()["holterMetrics"]["pauses"]["count"] == before["pauses"]["count"]
    await db.refresh(original)
    assert original.correction is None
    assert original.deleted_at is None


async def test_manual_pause_adds_evidence_and_rejection_releases_it(world):
    client, study, _, _ = world
    before = (await client.get(f"/studies/{study.id}/holter-metrics")).json()
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study, kind="pause"))
    assert response.status_code == 201, response.text
    assert response.json()["holterMetrics"]["pauses"]["count"] == before["pauses"]["count"] + 1
    finding_id = response.json()["finding"]["id"]
    discarded = await client.post(
        f"/studies/{study.id}/findings/{finding_id}/reclassification", json={"action": "reject"}
    )
    assert discarded.json()["holterMetrics"]["pauses"]["count"] == before["pauses"]["count"]


async def test_finalized_report_keeps_its_snapshot_and_pdf(world, db):
    from copy import deepcopy

    from app.db.models.study_clinical_report import StudyClinicalReport

    client, study, _, _ = world
    draft = await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Palpitaciones", "conclusion": "Revisado"},
    )
    preview = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()
    pdf = b"%PDF-1.4\n" + b"clinical report" * 4 + b"\n%%EOF"
    final = await client.post(
        f"/studies/{study.id}/clinical-report/finalize",
        params={"draftRevision": draft.json()["revision"], "snapshotHash": preview["snapshotHash"]},
        content=pdf,
        headers={"Content-Type": "application/pdf"},
    )
    assert final.status_code == 200, final.text
    stored = await db.get(StudyClinicalReport, uuid.UUID(final.json()["id"]))
    snapshot = deepcopy(stored.snapshot)
    assert (
        await client.post(f"/studies/{study.id}/findings", json=payload(study))
    ).status_code == 201
    await db.refresh(stored)
    assert stored.snapshot == snapshot
    downloaded = await client.get(f"/studies/{study.id}/clinical-reports/{stored.id}/pdf")
    assert downloaded.content == pdf


async def test_admin_can_create_in_an_open_study(world, make_user, as_user, db):
    from app.db.models.user import UserRole

    _, study, _, _ = world
    study.status = StudyStatus.IN_PROGRESS
    await db.commit()
    client = as_user(await make_user(role=UserRole.ADMIN))
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study))
    assert response.status_code == 201, response.text


async def test_legacy_demo_signal_attributes_the_real_batch(world, db):
    client, study, batch, _ = world
    study.ecg_s3_key = study.ecg_segments[0]["key"]
    study.ecg_segments = []
    batch.study_id = None
    batch.first_seq = None
    batch.batch_timestamp = int(study.started_at.timestamp())
    batch.num_samples = study.samples_count
    await db.commit()
    response = await client.post(f"/studies/{study.id}/findings", json=payload(study))
    assert response.status_code == 201, response.text
    event = await db.get(ECGEvent, uuid.UUID(response.json()["finding"]["id"]))
    assert event.batch_id == batch.id
    findings = (await client.get(f"/studies/{study.id}/findings")).json()
    assert any(item["id"] == str(event.id) for item in findings["ungrouped"])
