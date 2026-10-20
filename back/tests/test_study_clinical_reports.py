import uuid
from datetime import UTC, datetime, timedelta

from app.core.request_limits import MAX_CLINICAL_REPORT_PDF_BYTES
from app.db.models.study import StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment, TimeSyncSource
from app.db.models.user import User, UserRole
from app.modules.studies import studies_service
from app.modules.studies.studies_schemas import (
    StudyClinicalReportWindowPlanOut,
    StudyEcgAnnotationOut,
)
from app.modules.studies.studies_service import _touches_any


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
    assert studies_service._report_window_ranges(instant) == [(995_000, 1_005_000)]


def test_report_gives_short_findings_a_strip_with_context() -> None:
    # Una marca del pulsador dura una muestra (2 ms): la tira era de 2 ms y vacía.
    button = _annotation(kind="patient_marker", category="patient_marker", duration=2)
    brief = _annotation(duration=4_000)
    tail = _annotation(duration=60_002)

    assert studies_service._report_window_ranges(button) == [(993_000, 1_003_000)]
    assert studies_service._report_window_ranges(brief) == [(997_000, 1_007_000)]
    # Ningún bloque queda por debajo de los 10 s, tampoco el último de un hallazgo partido.
    assert [end - start for start, end in studies_service._report_window_ranges(tail)] == [
        60_000,
        10_000,
    ]


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


async def test_unverified_study_can_be_finalized_with_a_warning(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    study.started_at_verified = False
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))
    await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Palpitaciones", "conclusion": "Conclusión"},
    )
    preview = await client.get(f"/studies/{study.id}/clinical-report/preview")
    assert preview.status_code == 200
    body = preview.json()
    assert body["canFinalize"] is True
    assert body["snapshot"]["quality"]["timeVerified"] is False
    assert _time_issue(body)["severity"] == "warning"


def _time_issue(body: dict) -> dict:
    return next(issue for issue in body["issues"] if issue["code"] == "TIME_NOT_VERIFIED")


async def test_unknown_anchor_in_later_segment_is_marked_but_does_not_block(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    started_ms = int(study.started_at.timestamp() * 1000)
    for ordinal, matches_boot in enumerate((True, None)):
        start_ms = started_ms + ordinal * 1_000
        db.add(
            StudyTimelineSegment(
                study_id=study.id,
                ordinal=ordinal,
                boot_id=ordinal,
                start_sample_index=ordinal * 50,
                sample_count=50,
                start_epoch_ms=start_ms,
                end_epoch_ms=start_ms + 100,
                boot_epoch_ms=start_ms,
                anchor_matches_boot=matches_boot,
            )
        )
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))
    await client.put(
        f"/studies/{study.id}/clinical-report/draft",
        json={"revision": 0, "indication": "Palpitaciones", "conclusion": "Conclusión"},
    )

    preview = await client.get(f"/studies/{study.id}/clinical-report/preview")

    assert preview.status_code == 200
    body = preview.json()
    # Un tramo con la hora estimada no le quita el informe al médico
    # (`INTEGRACION.md`, pendiente 4): lo advierte y lo marca.
    assert body["canFinalize"] is True
    assert body["snapshot"]["schemaVersion"] == 4
    assert body["snapshot"]["quality"]["timeVerified"] is False
    assert _time_issue(body)["severity"] == "warning"
    later = started_ms + 1_000
    assert body["snapshot"]["quality"]["unverifiedSegments"] == [
        {"startEpochMs": later, "endEpochMs": later + 100, "uncertaintyMs": None}
    ]


async def test_a_segment_anchored_with_none_is_reported_as_estimated(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    started_ms = int(study.started_at.timestamp() * 1000)
    for ordinal, source in enumerate((TimeSyncSource.NTP, TimeSyncSource.NONE)):
        start_ms = started_ms + ordinal * 1_000
        db.add(
            StudyTimelineSegment(
                study_id=study.id,
                ordinal=ordinal,
                boot_id=ordinal,
                start_sample_index=ordinal * 50,
                sample_count=50,
                start_epoch_ms=start_ms,
                end_epoch_ms=start_ms + 100,
                boot_epoch_ms=start_ms,
                anchor_source=source,
                anchor_uncertainty_ms=1_200,
                anchor_matches_boot=True,
            )
        )
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))

    preview = await client.get(f"/studies/{study.id}/clinical-report/preview")

    body = preview.json()
    # El ancla es de su arranque, pero `none` es el `Date` de una respuesta HTTP:
    # no sale verificada sin decirlo (`INTEGRACION.md`, pendiente 3).
    assert body["snapshot"]["quality"]["timeVerified"] is False
    assert "hora del servidor, sin sincronización SNTP" in _time_issue(body)["message"]
    later = started_ms + 1_000
    assert body["snapshot"]["quality"]["unverifiedSegments"] == [
        {"startEpochMs": later, "endEpochMs": later + 100, "uncertaintyMs": 1_200}
    ]


async def test_coverage_counts_the_timeline_and_stops_at_the_last_sample(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    # El caso de `ee0880bc` (INTEGRACION.md, pendiente 4): un solo tramo, sin cortes,
    # una señal a ~498,85 muestras/s y 3 min de espera entre la última muestra y el
    # cierre. Daba 95,1 % de cobertura y 3 min 5 s de "interrupciones".
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    started = datetime.now(UTC) - timedelta(hours=2)
    recorded_ms = 3_577_000
    study = await make_study(
        patient,
        device,
        status=StudyStatus.COMPLETED,
        started_at=started,
        ended_at=started + timedelta(milliseconds=recorded_ms + 177_000),
        duration_ms=recorded_ms + 177_000,
        samples_count=round(recorded_ms * 498.85 / 1000),
        ecg_s3_key="studies/raw.f32",
        ecg_byte_length=400,
    )
    started_ms = int(started.timestamp() * 1000)
    db.add(
        StudyTimelineSegment(
            study_id=study.id,
            ordinal=0,
            boot_id=0,
            start_sample_index=0,
            sample_count=study.samples_count,
            start_epoch_ms=started_ms,
            end_epoch_ms=started_ms + recorded_ms,
            boot_epoch_ms=started_ms,
            anchor_source=TimeSyncSource.NTP,
            anchor_matches_boot=True,
        )
    )
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))

    quality = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()["snapshot"][
        "quality"
    ]

    assert quality["recordedMs"] == recorded_ms
    assert quality["wallClockMs"] == recorded_ms
    assert quality["interruptionMs"] == 0
    assert quality["coveragePercent"] == 100
    assert quality["afterLastSampleMs"] == 177_000


def test_time_verified_needs_an_anchor_of_its_boot_that_is_not_none() -> None:
    def segment(source: TimeSyncSource, matches: bool | None) -> StudyTimelineSegment:
        return StudyTimelineSegment(anchor_source=source, anchor_matches_boot=matches)

    assert segment(TimeSyncSource.NTP, True).time_verified
    assert not segment(TimeSyncSource.NONE, True).time_verified
    assert not segment(TimeSyncSource.NTP, None).time_verified
    assert not segment(TimeSyncSource.SERVER_RECEIVE, False).time_verified


async def test_verified_segments_need_no_warning_when_only_the_start_is_unconfirmed(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """`started_at` sin confirmar (su inicio cayó después de la recepción,
    dentro de la incertidumbre del puente) con todos los tramos verificados: la
    hora de las muestras es buena. Antes salía "1 tramo con la hora estimada"
    sin ningún tramo así, y el PDF marcaba estimadas todas las horas."""
    doctor, study = await _completed_study(make_doctor, make_patient, make_device, make_study)
    study.started_at_verified = False
    started_ms = int(study.started_at.timestamp() * 1000)
    db.add(
        StudyTimelineSegment(
            study_id=study.id,
            ordinal=0,
            boot_id=0,
            start_sample_index=0,
            sample_count=100,
            start_epoch_ms=started_ms,
            end_epoch_ms=started_ms + 200,
            boot_epoch_ms=started_ms,
            anchor_matches_boot=True,
        )
    )
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))

    body = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()

    assert body["snapshot"]["quality"]["timeVerified"] is True
    assert body["snapshot"]["quality"]["unverifiedSegments"] == []
    assert body["snapshot"]["study"]["startedAtVerified"] is False
    assert all(issue["code"] != "TIME_NOT_VERIFIED" for issue in body["issues"])


def test_only_the_strips_inside_an_unverified_segment_lose_their_clock() -> None:
    segment = StudyTimelineSegment(start_epoch_ms=10_000, end_epoch_ms=20_000)

    def window(start: int, end: int) -> StudyClinicalReportWindowPlanOut:
        return StudyClinicalReportWindowPlanOut(
            id="w",
            findingId=None,
            kind="hr_max",
            category="metric",
            severity="low",
            findingStartEpochMs=start,
            findingEndEpochMs=start,
            findingDurationMs=0,
            startEpochMs=start,
            endEpochMs=end,
            blockIndex=1,
            blockCount=1,
            confidenceScore=None,
            description=None,
            relatedSymptoms=[],
        )

    assert _touches_any(window(15_000, 16_000), [segment])
    assert _touches_any(window(5_000, 10_500), [segment])
    assert not _touches_any(window(0, 9_000), [segment])
    assert not _touches_any(window(21_000, 31_000), [segment])


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
