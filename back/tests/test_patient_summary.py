"""Resumen histórico, exclusiones del Holter y lecturas S3 acotadas."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.core.s3 import put_object
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User
from app.ml.beats import BEAT_DTYPE, encode_beats
from app.ml.holter_contracts import TimelineRun
from app.ml.holter_metrics import compute_holter_metrics
from app.modules.patients.patient_summary import _heart_rates, summary_window

RATE = 500


def test_window_preserves_gaps_drift_and_overlapping_anchors():
    runs = [TimelineRun(0, 1000, 0, 2004), TimelineRun(1000, 500, 3000, 4000)]
    window = summary_window(runs, 1002, 4000)
    assert window.spans == [(500, 1000), (1000, 1500)]
    assert window.recorded_ms == 2002
    overlapping = summary_window([*runs, TimelineRun(1500, 500, 3500, 4500)], 0, 4500)
    assert overlapping.recorded_ms == 3504


def test_summary_hr_matches_holter_with_exclusions_duplicates_and_pending_tail(monkeypatch):
    from app.modules.ingest import processing

    beats = np.empty(750, dtype=BEAT_DTYPE)
    beats["sample_index"] = np.arange(750) * 400
    beats["st_mv"] = 0
    beats = np.concatenate([beats, beats[100:101]])
    monkeypatch.setattr(processing, "load_beats", lambda *args, **kwargs: beats)
    study = Study(sample_rate=RATE, beats_analyzed_samples=240_000)
    runs = [TimelineRun(0, 300_000, 0, 600_000)]
    excluded = [(20_000, 40_000), (30_000, 50_000)]
    expected = compute_holter_metrics(beats, runs, excluded, [], RATE, 240_000)
    assert _heart_rates(study, [summary_window(runs, 0, 600_000)], excluded) == [
        expected["heartRate"]["averageBpm"]
    ]


async def test_closed_study_reads_only_window_and_reuses_cache(
    db, make_doctor, make_patient, make_device, make_study, as_user, s3, monkeypatch
):
    from app.modules.ingest import processing

    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    started = datetime(2025, 1, 1, tzinfo=UTC)
    hour_samples = 3600 * RATE
    study = await make_study(
        patient,
        device,
        status=StudyStatus.COMPLETED,
        started_at=started,
        ended_at=started + timedelta(days=5),
        samples_count=4 * hour_samples,
        beats_analyzed_samples=4 * hour_samples,
        ml_analyzed_samples=4 * hour_samples,
    )
    # El primer chunk está lejos de las ventanas y no existe en S3 a propósito.
    chunks = [{"key": "outside-window", "startSampleIndex": 0, "sampleCount": hour_samples}]
    for hour, bpm in [(2, 60), (3, 90)]:
        beats = np.empty(60 * bpm, dtype=BEAT_DTYPE)
        beats["sample_index"] = hour * hour_samples + np.arange(beats.size) * RATE * 60 // bpm
        beats["st_mv"] = 0
        key = f"studies/{study.id}/beats-{hour}"
        put_object(key, encode_beats(beats))
        chunks.append(
            {"key": key, "startSampleIndex": hour * hour_samples, "sampleCount": hour_samples}
        )
    study.ecg_beat_chunks = chunks
    await make_study(patient, device, status=StudyStatus.CANCELLED, samples_count=100)
    await db.flush()
    client = as_user(await db.get(User, doctor.user_id))
    reads = []
    original = processing.get_object

    def track(key):
        reads.append(key)
        return original(key)

    monkeypatch.setattr(processing, "get_object", track)
    for _ in range(2):
        response = await client.get(f"/patients/{patient.id}/summary?windowHours=1")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["studyId"] == str(study.id)
        assert data["windowEnd"] == "2025-01-01T04:00:00Z"
        assert data["heartRate"] == {"averageBpm": 90, "deltaBpm": 30, "trend": "up"}
        assert data["eventsDetected"]["count"] == 0
        assert data["adherencePercent"]["value"] == 100
    assert len(reads) == 2
    study.beats_analyzed_samples -= RATE
    await db.flush()
    assert (await client.get(f"/patients/{patient.id}/summary?windowHours=1")).status_code == 200
    assert len(reads) == 4
    other = await make_doctor()
    as_user(await db.get(User, other.user_id))
    assert (await client.get(f"/patients/{patient.id}/summary")).status_code == 404
    assert len(reads) == 4


async def test_empty_patient_has_no_fabricated_values(db, make_doctor, make_patient, as_user):
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    client = as_user(await db.get(User, doctor.user_id))
    response = await client.get(f"/patients/{patient.id}/summary")
    assert response.status_code == 200
    assert response.json() == {
        "windowHours": 24,
        "studyId": None,
        "windowStart": None,
        "windowEnd": None,
        "heartRate": None,
        "eventsDetected": None,
        "adherencePercent": None,
    }


def test_events_use_clinical_categories_and_doctor_reclassification():
    from app.db.models.ecg_event import ECGEvent, ECGEventType, ECGEventValidation
    from app.modules.patients.patient_summary import SummaryWindow, _event_count

    def event(kind, second=10, verdict=ECGEventValidation.PENDING, correction=None):
        return ECGEvent(
            event_type=kind,
            timestamp_in_recording=second,
            validation_status=verdict,
            correction=correction,
        )

    events = [
        event(ECGEventType.TACHYCARDIA),
        event(ECGEventType.PAUSE, verdict=ECGEventValidation.REJECTED),
        event(ECGEventType.NOISE),
        event(ECGEventType.PVC, correction={"effectiveKind": "normal_beats"}),
        event(
            ECGEventType.NOISE,
            verdict=ECGEventValidation.RECLASSIFIED,
            correction={"effectiveKind": "pause", "effectiveSeverity": "high"},
        ),
        event(ECGEventType.PAUSE, second=20),  # borde exclusivo del período
    ]
    window = SummaryWindow(0, 20_000, [(0, 20 * RATE)], 20_000)
    assert _event_count(events, window, RATE) == 2


async def test_short_unanalyzed_study_has_coverage_but_no_fake_hr_or_event_zero(
    db, make_doctor, make_patient, make_device, make_study, as_user
):
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    await make_study(patient, device, samples_count=60 * RATE)
    client = as_user(await db.get(User, doctor.user_id))
    response = await client.get(f"/patients/{patient.id}/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["heartRate"] is None
    assert data["eventsDetected"] is None
    assert data["adherencePercent"] == {"value": 100, "deltaPp": None, "trend": "flat"}
