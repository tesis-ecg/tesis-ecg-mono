"""La pirámide de niveles tiene que seguir creciendo lote a lote **en la base**.

`append_level_chunks` mutaba en el lugar los dicts que SQLAlchemy guarda como
valor original de la columna JSONB. Al flush la lista nueva y la original
comparaban iguales y no se emitía el UPDATE: la metadata se congelaba en el
último lote que agregó un nivel (el de 16384, apenas pasadas las 16.384
muestras), aunque los objetos de S3 se siguieran escribiendo. Los tests que no
recargan el estudio desde la base no lo ven, porque leen el objeto en memoria.
"""

from unittest.mock import patch

import pytest

from app.db.models.study import Study
from app.modules.ingest.processing import (
    BASE_BUCKET,
    PYRAMID_BUCKETS,
    append_filtered_view,
    process_batch,
    rebuild_level_metadata,
)
from tests.ingest_helpers import build_frames, post_frames

BATCHES = 6
SAMPLES = 24_000  # > 16.384: los seis niveles existen antes del último lote


async def _ingest_in_batches(client, db, make_patient, make_device) -> str:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(SAMPLES)
    size = -(-len(frames) // BATCHES)
    study_id: str | None = None
    for start in range(0, len(frames), size):
        body = (await post_frames(client, device, api_key, frames[start : start + size])).json()
        study_id = body["studyId"]
        await process_batch(db, body["batchId"])
        study = await db.get(Study, study_id)
        assert study is not None
        with patch("app.modules.ingest.processing.CONTEXT_SECONDS", 0):
            await append_filtered_view(db, study)
        await db.commit()
        # Lo que cuenta es lo que quedó en la base, no el objeto en memoria.
        db.expunge_all()
    assert study_id is not None
    return study_id


def _covered_samples(levels: list[dict], bucket: int) -> int:
    level = next(item for item in levels if int(item["samplesPerBucket"]) == bucket)
    assert int(level["pointCount"]) == sum(int(c["pointCount"]) for c in level["chunks"])
    return int(level["pointCount"]) // 2 * bucket


async def test_the_raw_pyramid_keeps_growing_after_every_level_exists(
    client, s3, db, make_patient, make_device
) -> None:
    study_id = await _ingest_in_batches(client, db, make_patient, make_device)
    study = await db.get(Study, study_id)
    assert study is not None
    assert study.samples_count == SAMPLES

    covered = _covered_samples(study.ecg_pyramid_levels, BASE_BUCKET)
    assert covered == (SAMPLES // BASE_BUCKET) * BASE_BUCKET
    base = next(lv for lv in study.ecg_pyramid_levels if lv["samplesPerBucket"] == BASE_BUCKET)
    assert len(base["chunks"]) == BATCHES


async def test_the_filtered_pyramid_keeps_growing_after_every_level_exists(
    client, s3, db, make_patient, make_device
) -> None:
    study_id = await _ingest_in_batches(client, db, make_patient, make_device)
    study = await db.get(Study, study_id)
    assert study is not None
    assert study.filter_view_enabled

    covered = _covered_samples(study.ecg_filtered_pyramid_levels, BASE_BUCKET)
    assert covered == (study.filtered_samples_count // BASE_BUCKET) * BASE_BUCKET
    assert study.filtered_samples_count == SAMPLES


async def test_rebuild_level_metadata_recovers_a_frozen_pyramid(
    client, s3, db, make_patient, make_device
) -> None:
    """Los estudios ya congelados se reparan desde los objetos de S3."""
    study_id = await _ingest_in_batches(client, db, make_patient, make_device)
    study = await db.get(Study, study_id)
    assert study is not None
    healthy = study.ecg_pyramid_levels
    healthy_filtered = study.ecg_filtered_pyramid_levels

    # Simula el estado que dejó el bug: cada nivel con solo su primer chunk.
    def frozen(levels: list[dict]) -> list[dict]:
        return [
            {**lv, "chunks": lv["chunks"][:1], "pointCount": lv["chunks"][0]["pointCount"]}
            for lv in levels
        ]

    study.ecg_pyramid_levels = frozen(healthy)
    study.ecg_filtered_pyramid_levels = frozen(healthy_filtered)
    await db.commit()
    db.expunge_all()
    study = await db.get(Study, study_id)
    assert study is not None

    rebuilt = rebuild_level_metadata(study)

    assert rebuilt == healthy
    assert rebuild_level_metadata(study, filtered=True) == healthy_filtered
    assert {int(level["samplesPerBucket"]) for level in rebuilt} <= set(PYRAMID_BUCKETS)


async def test_rebuild_level_metadata_refuses_an_incomplete_pyramid(
    client, s3, db, make_patient, make_device
) -> None:
    """Si S3 no cubre lo procesado, no se inventa una pirámide."""
    study_id = await _ingest_in_batches(client, db, make_patient, make_device)
    study = await db.get(Study, study_id)
    assert study is not None
    study.samples_count += 10 * 16384

    with pytest.raises(ValueError):
        rebuild_level_metadata(study)
