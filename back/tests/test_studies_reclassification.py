"""`POST /studies/{id}/findings/{finding_id}/reclassification` y su efecto en las métricas."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from app.core.s3 import put_object
from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import (
    ECGEvent,
    ECGEventSeverity,
    ECGEventType,
    ECGEventValidation,
)
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User, UserRole
from app.modules.ingest.processing import append_beat_analysis, segment_key
from app.modules.studies.corrections import (
    InvalidReclassification,
    doctor_corrections,
    is_reclassifiable,
    plan_reclassification,
)
from tests.synthetic_ecg import beat_times, synthetic_ecg

RATE = 500
PAUSE_INDEX = 200


async def _user_of(db, doctor) -> User:  # type: ignore[no-untyped-def]
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


async def _study(db, make_doctor, make_patient, make_device, make_study):  # type: ignore[no-untyped-def]
    """Estudio cerrado de 6 min a 75 lpm con una pausa de 2,6 s, ya analizado."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    rr = np.full(450, 0.8)
    rr[PAUSE_INDEX] = 2.6
    times = beat_times(rr)
    signal = synthetic_ecg(times, times[-1] + 2.0, RATE, noise_mv=0.02).astype("<f4")
    started = datetime.now(UTC) - timedelta(hours=2)
    study: Study = await make_study(
        patient,
        device,
        status=StudyStatus.COMPLETED,
        started_at=started,
        ended_at=started + timedelta(minutes=10),
        duration_ms=signal.size * 1000 // RATE,
        samples_count=int(signal.size),
    )
    key = segment_key(study.id, 0)
    put_object(key, signal.tobytes())
    study.ecg_segments = [
        {
            "key": key,
            "startSampleIndex": 0,
            "sampleCount": int(signal.size),
            "byteLength": signal.size * 4,
            "sha256": hashlib.sha256(signal.tobytes()).hexdigest(),
        }
    ]
    await append_beat_analysis(db, study, max_samples=None)
    batch = ECGBatch(
        device_id=device.id,
        study_id=study.id,
        received_at=datetime.now(UTC),
        batch_timestamp=0,
        duration_seconds=4,
        sample_rate=RATE,
        num_channels=1,
        num_samples=1000,
        compression_type="raw-f32",
        s3_key=f"batches/{device.id}/{uuid.uuid4().hex}.f32",
        processing_status=ProcessingStatus.DONE,
    )
    db.add(batch)
    await db.flush()
    await db.commit()
    return doctor, study, batch, times


def _finding(
    batch: ECGBatch,
    study: Study,
    kind: str,
    event_type: ECGEventType,
    start: int,
    count: int,
    **extra: object,
) -> ECGEvent:
    metadata: dict[str, object] = {
        "kind": kind,
        "startSampleIndex": start,
        "sampleCount": count,
        "source": "ml",
    }
    metadata.update(extra.pop("metadata", {}))  # type: ignore[arg-type]
    return ECGEvent(
        batch_id=batch.id,
        study_id=study.id,
        event_type=event_type,
        severity=ECGEventSeverity.MEDIUM,
        timestamp_in_recording=start / RATE,
        duration_seconds=count / RATE,
        confidence_score=0.8,
        event_metadata=metadata,
        model_version=extra.pop("model_version", "ml-test"),  # type: ignore[arg-type]
        dedupe_key=f"{kind}:{start}",
        **extra,
    )


async def _pause_finding(db, batch, study, times) -> ECGEvent:  # type: ignore[no-untyped-def]
    # `rr[PAUSE_INDEX]` es el intervalo que sigue al latido PAUSE_INDEX.
    start = int(times[PAUSE_INDEX] * RATE)
    end = int(times[PAUSE_INDEX + 1] * RATE)
    event = _finding(
        batch, study, "pause", ECGEventType.PAUSE, start, end - start, metadata={"beatCount": 2}
    )
    db.add(event)
    await db.commit()
    return event


def _url(study: Study, event: ECGEvent) -> str:
    return f"/studies/{study.id}/findings/{event.id}/reclassification"


async def test_el_medico_marca_ruido_y_la_pausa_sale_de_las_metricas(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    user = await _user_of(db, doctor)
    client = as_user(user)
    before = (await client.get(f"/studies/{study.id}/holter-metrics")).json()
    assert before["pauses"]["count"] == 1

    response = await client.post(
        _url(study, event), json={"action": "noise", "note": "Artefacto por movimiento"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    finding = body["finding"]
    assert finding["id"] == str(event.id)
    assert finding["kind"] == "pause", "el tipo original no se pisa"
    assert finding["validationStatus"] == "noise"
    assert finding["correction"]["action"] == "noise"
    assert finding["correction"]["effectiveKind"] is None
    assert finding["correction"]["note"] == "Artefacto por movimiento"
    assert finding["correction"]["correctedBy"] == str(user.id)
    assert finding["correction"]["correctedByName"] == user.full_name
    assert finding["correction"]["correctedAt"] is not None
    # Las métricas recalculadas viajan en la respuesta, en el formato de
    # /holter-metrics, y son las mismas que devuelve esa ruta después.
    assert body["holterMetrics"]["status"] == "ok"
    assert body["holterMetrics"]["pauses"]["count"] == 0
    after = (await client.get(f"/studies/{study.id}/holter-metrics")).json()
    assert after["pauses"]["count"] == 0
    assert after["heartRate"]["totalBeats"] < before["heartRate"]["totalBeats"]
    assert after["analysis"]["doctorCorrections"]["findings"] == 1


async def test_rechazar_una_pausa_y_restablecerla(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    client = as_user(await _user_of(db, doctor))

    rejected = await client.post(_url(study, event), json={"action": "reject"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["finding"]["validationStatus"] == "rejected"
    assert rejected.json()["holterMetrics"]["pauses"]["count"] == 0

    reset = await client.post(_url(study, event), json={"action": "reset"})
    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert body["finding"]["validationStatus"] == "pending"
    assert body["finding"]["correction"] is None
    assert body["holterMetrics"]["pauses"]["count"] == 1
    await db.refresh(event)
    assert event.validated_by is None and event.validated_at is None
    assert event.correction is None


async def test_confirmar_guarda_quien_y_cuando_y_deja_auditoria(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    user = await _user_of(db, doctor)
    client = as_user(user)

    response = await client.post(_url(study, event), json={"action": "confirm"})

    assert response.status_code == 200, response.text
    await db.refresh(event)
    assert event.validation_status is ECGEventValidation.CONFIRMED
    assert event.validated_by == user.id
    assert event.validated_at is not None
    assert event.event_type is ECGEventType.PAUSE
    assert event.correction is not None and event.correction["action"] == "confirm"
    audit = (
        await db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.ECG_EVENT_RECLASSIFIED)
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].user_id == user.id
    assert audit[0].event_metadata is not None
    assert audit[0].event_metadata["target_study_id"] == str(study.id)
    assert audit[0].event_metadata["event_id"] == str(event.id)
    assert audit[0].event_metadata["action"] == "confirm"
    assert audit[0].event_metadata["previous_status"] == "pending"
    # Y la lista de hallazgos lo muestra, con el nombre de quien corrigió.
    findings = (await client.get(f"/studies/{study.id}/findings")).json()
    corrected = [
        item
        for group in findings["groups"]
        for item in group["items"] + findings["ungrouped"]
        if item["id"] == str(event.id)
    ]
    assert corrected and corrected[0]["correction"]["correctedByName"] == user.full_name
    assert corrected[0]["reclassifiable"] is True


async def test_un_admin_sin_perfil_de_medico_tambien_reclasifica(
    db, s3, as_user, make_user, make_doctor, make_patient, make_device, make_study
) -> None:
    _, study, batch, times = await _study(db, make_doctor, make_patient, make_device, make_study)
    event = await _pause_finding(db, batch, study, times)
    admin = await make_user(UserRole.ADMIN)
    await db.commit()

    response = await as_user(admin).post(_url(study, event), json={"action": "confirm"})

    assert response.status_code == 200, response.text
    await db.refresh(event)
    assert event.validated_by == admin.id, "validated_by apunta a user, no a doctor"


async def test_otro_medico_recibe_404(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    _, study, batch, times = await _study(db, make_doctor, make_patient, make_device, make_study)
    event = await _pause_finding(db, batch, study, times)
    intruder = await _user_of(db, await make_doctor())
    await db.commit()

    response = await as_user(intruder).post(_url(study, event), json={"action": "noise"})

    assert response.status_code == 404
    await db.refresh(event)
    assert event.validation_status is ECGEventValidation.PENDING
    assert event.correction is None


async def test_un_hallazgo_de_otro_estudio_o_borrado_es_404(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    other_device, _ = await make_device(patient=await make_patient(doctor=doctor))
    other_study = await make_study(await db.get(Patient, other_device.patient_id), other_device)
    gone = _finding(batch, study, "pause", ECGEventType.PAUSE, 10, 100)
    gone.deleted_at = datetime.now(UTC)
    db.add(gone)
    await db.commit()
    client = as_user(await _user_of(db, doctor))

    foreign = await client.post(
        f"/studies/{other_study.id}/findings/{event.id}/reclassification",
        json={"action": "confirm"},
    )
    deleted = await client.post(_url(study, gone), json={"action": "confirm"})
    unknown = await client.post(
        f"/studies/{study.id}/findings/{uuid.uuid4()}/reclassification",
        json={"action": "confirm"},
    )

    assert foreign.status_code == deleted.status_code == unknown.status_code == 404


async def test_hallazgos_que_el_motor_no_escribio_o_de_alcance_estudio_no_se_reclasifican(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, _ = await _study(db, make_doctor, make_patient, make_device, make_study)
    manual = _finding(batch, study, "afib", ECGEventType.AFIB, 100, 500, model_version=None)
    study_wide = _finding(
        batch,
        study,
        "recurrent_morphology",
        ECGEventType.ANOMALY,
        0,
        5000,
        metadata={"scope": "study"},
    )
    no_span = _finding(batch, study, "pause", ECGEventType.PAUSE, 0, 0)
    no_span.event_metadata = {"kind": "pause", "source": "ml"}
    db.add_all([manual, study_wide, no_span])
    await db.commit()
    client = as_user(await _user_of(db, doctor))

    for event in (manual, study_wide, no_span):
        response = await client.post(_url(study, event), json={"action": "confirm"})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "FINDING_NOT_RECLASSIFIABLE"


@pytest.mark.parametrize(
    ("kind", "event_type", "body"),
    [
        ("pause", ECGEventType.PAUSE, {"action": "relabel", "beatLabel": "V"}),
        ("pause", ECGEventType.PAUSE, {"action": "confirm", "beatLabel": "V"}),
        ("pause", ECGEventType.PAUSE, {"action": "relabel"}),
        ("pause", ECGEventType.PAUSE, {"action": "reset", "beatLabel": "N"}),
        ("ventricular_ectopy", ECGEventType.ECTOPY, {"action": "relabel", "beatLabel": "V"}),
        ("tachycardia", ECGEventType.TACHYCARDIA, {"action": "relabel", "beatLabel": "N"}),
    ],
)
async def test_combinaciones_invalidas_dan_422(
    kind, event_type, body, db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, _ = await _study(db, make_doctor, make_patient, make_device, make_study)
    event = _finding(batch, study, kind, event_type, 1000, 2000)
    db.add(event)
    await db.commit()

    response = await as_user(await _user_of(db, doctor)).post(_url(study, event), json=body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "INVALID_RECLASSIFICATION"
    await db.refresh(event)
    assert event.correction is None


async def test_reclasificar_ectopia_v_a_s_deja_el_original_y_lo_dice_el_manifest(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, _ = await _study(db, make_doctor, make_patient, make_device, make_study)
    event = _finding(batch, study, "ventricular_ectopy", ECGEventType.ECTOPY, 1000, 3000)
    db.add(event)
    await db.commit()
    client = as_user(await _user_of(db, doctor))

    response = await client.post(_url(study, event), json={"action": "relabel", "beatLabel": "S"})

    assert response.status_code == 200, response.text
    finding = response.json()["finding"]
    assert finding["kind"] == "ventricular_ectopy"
    assert finding["category"] == "clinical", "ECTOPY es clínico por tipo"
    assert finding["validationStatus"] == "reclassified"
    assert finding["correction"]["beatLabel"] == "S"
    assert finding["correction"]["effectiveKind"] == "supraventricular_ectopy"
    await db.refresh(event)
    assert event.event_type is ECGEventType.ECTOPY
    assert event.correction is not None
    assert (event.correction["fromLabel"], event.correction["toLabel"]) == ("V", "S")
    assert (event.correction["spanStart"], event.correction["spanEnd"]) == (1000, 4000)
    manifest = (await client.get(f"/studies/{study.id}/ecg/manifest")).json()
    annotation = next(item for item in manifest["annotations"] if item["id"] == str(event.id))
    assert annotation["kind"] == "ventricular_ectopy"
    assert annotation["validationStatus"] == "reclassified"
    assert annotation["effectiveKind"] == "supraventricular_ectopy"


async def test_la_correccion_cambia_el_hash_de_la_vista_previa_del_informe(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Una vista previa vieja choca con `REPORT_SNAPSHOT_CONFLICT` al finalizar."""
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    client = as_user(await _user_of(db, doctor))
    before = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()

    await client.post(_url(study, event), json={"action": "noise"})
    after = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()

    assert before["snapshotHash"] != after["snapshotHash"]


# --- Unidades sin base de datos ------------------------------------------- #


def _event(correction: dict | None, *, when: int = 0) -> ECGEvent:
    event = ECGEvent(
        event_type=ECGEventType.ECTOPY,
        severity=ECGEventSeverity.LOW,
        timestamp_in_recording=0.0,
        correction=correction,
        validated_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=when),
    )
    return event


def test_plan_de_cada_accion() -> None:
    span = (100, 900)
    relabel = plan_reclassification("vt_run", span, "relabel", "S")
    assert (relabel.from_label, relabel.to_label, relabel.effective_kind) == ("V", "S", "svt_run")
    to_normal = plan_reclassification("ventricular_ectopy", span, "relabel", "N")
    assert to_normal.effective_kind == "normal_beats"
    tachy = plan_reclassification("tachycardia", span, "relabel", "V")
    assert (tachy.from_label, tachy.effective_kind) == ("N", "vt_run")
    rejected = plan_reclassification("supraventricular_ectopy", span, "reject", None)
    assert (rejected.from_label, rejected.to_label, rejected.effective_kind) == ("S", "N", None)
    assert plan_reclassification("tachycardia", span, "reject", None).to_label is None
    assert plan_reclassification("pause", span, "noise", None).effective_kind is None
    assert plan_reclassification("pause", span, "confirm", None).effective_kind == "pause"
    with pytest.raises(InvalidReclassification):
        plan_reclassification("pause", span, "relabel", "S")


def test_doctor_corrections_arma_lo_que_consume_el_recalculo() -> None:
    def correction(action: str, kind: str, label_from=None, label_to=None, start=0, end=500):  # type: ignore[no-untyped-def]
        return {
            "action": action,
            "fromKind": kind,
            "fromLabel": label_from,
            "toLabel": label_to,
            "effectiveKind": None,
            "spanStart": start,
            "spanEnd": end,
        }

    events = [
        _event(correction("relabel", "ventricular_ectopy", "V", "S", 0, 500), when=2),
        _event(correction("relabel", "ventricular_ectopy", "V", "N", 100, 300), when=1),
        _event(correction("noise", "ventricular_ectopy", start=1000, end=2000)),
        _event(correction("reject", "noise_burst", start=3000, end=4000)),
        _event(correction("reject", "pause", start=5000, end=6000)),
        _event(correction("reject", "supraventricular_ectopy", "S", "N", 7000, 7500)),
        _event(correction("confirm", "pause", start=8000, end=9000)),
        _event(None),
    ]

    result = doctor_corrections(events)

    assert result.noise_spans == ((1000, 2000),)
    assert result.readable_spans == ((3000, 4000),)
    assert result.rejected_pause_spans == ((5000, 6000),)
    # Ordenados por validated_at: gana la última decisión sobre un latido.
    assert [(item.from_label, item.to_label, item.start_sample) for item in result.relabels] == [
        ("S", "N", 7000),
        ("V", "N", 100),
        ("V", "S", 0),
    ]
    assert result.corrected_findings == 6, "el «confirmar» no cambia ninguna cifra"


def test_solo_se_reclasifica_lo_que_escribio_el_motor_con_un_tramo_propio() -> None:
    def event(model_version: str | None, metadata: dict) -> ECGEvent:
        return ECGEvent(
            event_type=ECGEventType.ECTOPY,
            severity=ECGEventSeverity.LOW,
            timestamp_in_recording=0.0,
            model_version=model_version,
            event_metadata=metadata,
        )

    span = {"startSampleIndex": 100, "sampleCount": 800}
    assert is_reclassifiable(event("ml-1", span))
    assert not is_reclassifiable(event(None, span))
    assert not is_reclassifiable(event("ml-1", {"startSampleIndex": 100}))
    assert not is_reclassifiable(event("ml-1", {**span, "scope": "study"}))


@pytest.mark.parametrize(
    ("target", "severity"),
    [("av_block_complete", "critical"), ("afib", "high"), ("noise_burst", "low"), ("pvc", "high")],
)
async def test_el_medico_elige_cualquier_tipo_y_gravedad_sin_pisar_el_original(
    target, severity, db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    client = as_user(await _user_of(db, doctor))
    response = await client.post(
        _url(study, event),
        json={"action": "relabel", "kind": target, "severity": severity, "note": "Revisado"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    finding = body["finding"]
    assert finding["kind"] == "pause"
    assert finding["severity"] == severity
    assert finding["correction"]["effectiveKind"] == target
    assert finding["correction"]["effectiveSeverity"] == severity
    assert body["holterMetrics"]["pauses"]["count"] == 0
    await db.refresh(event)
    assert event.event_type is ECGEventType.PAUSE
    assert event.severity is ECGEventSeverity.MEDIUM
    manifest = (await client.get(f"/studies/{study.id}/ecg/manifest")).json()
    annotation = next(item for item in manifest["annotations"] if item["id"] == str(event.id))
    assert annotation["effectiveKind"] == target
    assert annotation["severity"] == severity
    assert annotation["category"] == ("signal_quality" if target == "noise_burst" else "clinical")
    preview = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()
    assert target in str(preview), "el informe aplica el tipo corregido"
    reset = await client.post(_url(study, event), json={"action": "reset"})
    assert reset.json()["finding"]["severity"] == "medium"


@pytest.mark.parametrize(
    "body",
    [
        {"action": "relabel", "kind": "afib"},
        {"action": "relabel", "severity": "high"},
        {"action": "relabel", "kind": "inventado", "severity": "high"},
        {"action": "relabel", "kind": "afib", "severity": "inventada"},
        {"action": "reject", "kind": "afib", "severity": "high"},
        {"action": "relabel", "kind": "afib", "severity": "high", "beatLabel": "S"},
    ],
)
async def test_valida_tipo_y_gravedad_del_hallazgo(
    body, db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    event = await _pause_finding(db, batch, study, times)
    response = await as_user(await _user_of(db, doctor)).post(_url(study, event), json=body)
    assert response.status_code == 422, response.text
    await db.refresh(event)
    assert event.correction is None


def test_un_trastorno_de_conduccion_se_puede_cambiar_a_ectopia_o_pausa() -> None:
    plan = plan_reclassification(
        "av_block_complete",
        (0, 1500),
        "relabel",
        None,
        target_kind="ventricular_ectopy",
        severity="high",
    )
    corrections = doctor_corrections([_event(plan.as_json())])
    assert {relabel.from_label for relabel in corrections.relabels} == {"N", "S", "V"}
    assert {relabel.to_label for relabel in corrections.relabels} == {"V"}
    pause = plan_reclassification(
        "av_block_complete",
        (0, 1500),
        "relabel",
        None,
        target_kind="pause",
        severity="critical",
    )
    assert doctor_corrections([_event(pause.as_json())]).confirmed_pause_spans == ((0, 1500),)
