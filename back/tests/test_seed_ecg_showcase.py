import numpy as np
from sqlalchemy import func, select

from app.core.s3 import get_object, list_keys
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity
from app.db.models.patient import Patient
from app.db.models.study import Study
from app.scripts.seed_ecg_showcase import (
    SHOWCASE_EVENTS,
    SHOWCASE_MRN,
    SHOWCASE_PREFIX,
    build_showcase_signal,
    seed_showcase,
)


def test_showcase_signal_is_deterministic_and_contains_visible_artifacts() -> None:
    first = build_showcase_signal()
    second = build_showcase_signal()

    assert np.array_equal(first, second)
    assert first.dtype == np.dtype("<f4")
    assert np.std(first[150 * 250 : 165 * 250]) < 0.03
    assert np.allclose(np.unique(first[385 * 250 : 395 * 250]), [-2.4, 2.4])


async def test_showcase_seed_is_additive_and_idempotent(s3, db, make_user, make_patient) -> None:
    await make_user(email="dev@tesis.com")
    unrelated = await make_patient(medical_record_num="DO-NOT-DELETE")

    first = await seed_showcase(db, "dev@tesis.com")
    second = await seed_showcase(db, "dev@tesis.com")

    assert first.id != second.id
    assert (
        await db.scalar(
            select(func.count())
            .select_from(Patient)
            .where(Patient.medical_record_num == SHOWCASE_MRN)
        )
        == 1
    )
    assert await db.get(Patient, unrelated.id) is not None

    batch = await db.scalar(select(ECGBatch).where(ECGBatch.study_id == second.id))
    assert batch is not None
    events = list((await db.scalars(select(ECGEvent).where(ECGEvent.batch_id == batch.id))).all())
    assert len(events) == len(SHOWCASE_EVENTS)
    assert {event.severity for event in events} == {
        ECGEventSeverity.LOW,
        ECGEventSeverity.MEDIUM,
        ECGEventSeverity.HIGH,
        ECGEventSeverity.CRITICAL,
    }

    keys = list_keys(SHOWCASE_PREFIX)
    assert len(keys) > 1
    study = await db.get(Study, second.id)
    assert study is not None and study.ecg_s3_key in keys
    assert len(get_object(study.ecg_s3_key)) == study.samples_count * 4
