import uuid
from datetime import UTC, datetime, timedelta

from app.core.request_limits import MAX_CLINICAL_REPORT_PDF_BYTES
from app.db.models.study import StudyStatus
from app.db.models.user import User, UserRole
from app.modules.studies import studies_service
from app.modules.studies.studies_schemas import StudyEcgAnnotationOut


def _annotation(
    *,
    kind: str = "tachycardia",
    category: str = "clinical",
    severity: str = "medium",
    start: int = 1_000_000,
    duration: int = 10_000,
    confidence: float | None = 0.8,
    linked_to: uuid.UUID | None = None,
) -> StudyEcgAnnotationOut:
    return StudyEcgAnnotationOut(
        id=uuid.uuid4(),
        kind=kind,
        category=category,
        severity=severity,
        startOffsetMs=0,
        endOffsetMs=duration,
        startEpochMs=start,
        endEpochMs=start + duration,
        confidenceScore=confidence,
        linkedAnnotationId=linked_to,
        description=None,
    )


def test_report_selects_three_per_type_and_excludes_technical_events() -> None:
    findings = [
        _annotation(start=1_000_000 + index * 20_000, severity="high" if index == 3 else "low")
        for index in range(4)
    ]
    findings.extend(
        [
            _annotation(kind="noise", category="signal_quality"),
            _annotation(kind="internal_gap", category="technical"),
        ]
    )

    selected = studies_service._selected_report_findings(findings)

    assert len(selected) == 3
    assert all(item.category == "clinical" for item in selected)
    assert findings[3] in selected


def test_report_prioritizes_findings_with_patient_response() -> None:
    linked = _annotation(severity="low", start=2_000_000)
    others = [_annotation(severity="critical", start=3_000_000 + index) for index in range(3)]
    response = _annotation(
        kind="patient_report",
        category="patient_marker",
        duration=0,
        linked_to=linked.id,
    )

    selected = studies_service._selected_report_findings([*others, linked, response])

    assert linked in selected
    assert response not in selected


def test_report_splits_short_and_long_findings_into_bounded_windows() -> None:
    short = _annotation(duration=150_000)
    long = _annotation(duration=600_000)
    instant = _annotation(duration=0)

    assert [end - start for start, end in studies_service._report_window_ranges(short)] == [
        60_000,
        60_000,
        30_000,
    ]
    long_ranges = studies_service._report_window_ranges(long)
    assert len(long_ranges) == 4
    assert all(end - start == 60_000 for start, end in long_ranges)
    assert studies_service._report_window_ranges(instant) == [(999_500, 1_000_500)]


async def _doctor_user(db, doctor) -> User:  # type: ignore[no-untyped-def]
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


async def _completed_study(make_doctor, make_patient, make_device, make_study):  # type: ignore[no-untyped-def]
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    started = datetime.now(UTC) - timedelta(hours=1)
    study = await make_study(
        patient,
        device,
        status=StudyStatus.COMPLETED,
        started_at=started,
        ended_at=started + timedelta(hours=1),
        duration_ms=3_600_000,
        samples_count=100,
        ecg_s3_key="studies/raw.f32",
        ecg_byte_length=400,
    )
    return doctor, study


async def test_draft_uses_optimistic_revision_and_is_isolated_by_doctor(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    intruder = await make_doctor()
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))

    created = await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Palpitaciones", "conclusion": "Conclusión"},
    )
    assert created.status_code == 200
    assert created.json()["revision"] == 1

    stale = await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Otro", "conclusion": "Otra"},
    )
    assert stale.status_code == 409

    hidden = await as_user(await _doctor_user(db, intruder)).get(
        f"/studies/{study.id}/clinical-report/draft"
    )
    assert hidden.status_code == 404


async def test_final_report_is_immutable_versioned_and_downloads_exact_pdf(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))
    draft = await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Palpitaciones", "conclusion": "Conclusión"},
    )
    preview = await client.get(f"/studies/{study.id}/clinical-report/preview")
    assert preview.status_code == 200
    body = preview.json()
    pdf = b"%PDF-1.4\n" + b"clinical report" * 4 + b"\n%%EOF"

    finalized = await client.post(
        f"/studies/{study.id}/clinical-report/finalize",
        params={
            "draftRevision": draft.json()["revision"],
            "snapshotHash": body["snapshotHash"],
        },
        content=pdf,
        headers={"Content-Type": "application/pdf"},
    )
    assert finalized.status_code == 200
    assert finalized.json()["version"] == 1

    versions = await client.get(f"/studies/{study.id}/clinical-reports")
    assert [item["version"] for item in versions.json()["items"]] == [1]
    downloaded = await client.get(
        f"/studies/{study.id}/clinical-reports/{finalized.json()['id']}/pdf"
    )
    assert downloaded.content == pdf
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["content-disposition"].startswith("attachment;")


async def test_finalization_rejects_open_simulated_invalid_and_stale_reports(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    open_study = await make_study(
        patient,
        device,
        samples_count=10,
        ecg_s3_key="raw.f32",
        is_simulated=True,
    )
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))
    draft = await client.put(
        f"/studies/{open_study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Prueba", "conclusion": "Prueba"},
    )
    preview = await client.get(f"/studies/{open_study.id}/clinical-report/preview")
    assert preview.json()["canFinalize"] is False
    assert any(issue["code"] == "SIMULATED_STUDY" for issue in preview.json()["issues"])

    invalid = await client.post(
        f"/studies/{open_study.id}/clinical-report/finalize",
        params={
            "draftRevision": draft.json()["revision"],
            "snapshotHash": preview.json()["snapshotHash"],
        },
        content=b"not a pdf",
        headers={"Content-Type": "application/pdf"},
    )
    assert invalid.status_code == 422

    stale = await client.post(
        f"/studies/{open_study.id}/clinical-report/finalize",
        params={"draftRevision": draft.json()["revision"], "snapshotHash": "0" * 64},
        content=b"%PDF-1.4\n" + b"x" * 40 + b"\n%%EOF",
        headers={"Content-Type": "application/pdf"},
    )
    assert stale.status_code == 409


async def test_completed_simulated_study_can_generate_a_final_report(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    study.is_simulated = True
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))
    draft = await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Prueba", "conclusion": "Informe de simulación"},
    )
    preview = await client.get(f"/studies/{study.id}/clinical-report/preview")
    assert preview.json()["canFinalize"] is True
    assert any(
        issue["code"] == "SIMULATED_STUDY" and issue["severity"] == "warning"
        for issue in preview.json()["issues"]
    )

    finalized = await client.post(
        f"/studies/{study.id}/clinical-report/finalize",
        params={
            "draftRevision": draft.json()["revision"],
            "snapshotHash": preview.json()["snapshotHash"],
        },
        content=b"%PDF-1.4\n" + b"x" * 40 + b"\n%%EOF",
        headers={"Content-Type": "application/pdf"},
    )
    assert finalized.status_code == 200


async def test_admin_can_edit_a_report_draft(
    db, as_user, make_user, make_doctor, make_patient, make_device, make_study
) -> None:
    _, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    admin = await make_user(UserRole.ADMIN)
    await db.commit()

    response = await as_user(admin).put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Control", "conclusion": "Normal"},
    )

    assert response.status_code == 200


async def test_clinical_report_rejects_a_body_larger_than_the_pdf_limit_before_parsing(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/studies/{study.id}/clinical-report/finalize",
        content=b"",
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(MAX_CLINICAL_REPORT_PDF_BYTES + 1),
        },
    )

    assert response.status_code == 413
    assert response.json()["code"] == "REPORT_TOO_LARGE"
