"""Análisis de latidos durante la ingesta: cursor, contexto, tope y compactación."""

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.study import Study, StudyStatus
from app.modules.ingest import processing
from app.modules.ingest.processing import (
    append_beat_analysis,
    compact_beat_chunks,
    load_beats,
    process_batch,
)
from tests.ingest_helpers import build_frames, post_frames

#: `synth_samples` dibuja un latido cada 250 muestras (120 lpm a 500 SPS).
PERIOD = 250


async def _ingest(client, db: AsyncSession, make_patient, make_device, seconds: int) -> Study:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(seconds * 500)
    third = len(frames) // 3
    study_id = None
    for start in range(0, len(frames), third):
        body = (await post_frames(client, device, api_key, frames[start : start + third])).json()
        await process_batch(db, body["batchId"])
        study_id = body["studyId"]
    study = await db.get(Study, study_id)
    assert study is not None
    await db.refresh(study)
    return study


def _assert_regular(beats: np.ndarray) -> None:
    spacing = np.diff(beats["sample_index"])
    assert spacing.size > 0
    assert np.all(np.abs(spacing - PERIOD) <= 2), spacing


async def test_la_ingesta_deja_latidos_y_espera_contexto_en_la_cola_del_tramo(
    client, s3, db, make_patient, make_device
) -> None:
    study = await _ingest(client, db, make_patient, make_device, seconds=90)

    context = processing.BEAT_CONTEXT_SECONDS * 500
    assert study.beats_analyzed_samples == study.samples_count - context
    beats = load_beats(study)
    _assert_regular(beats)
    assert beats["sample_index"].max() < study.beats_analyzed_samples
    assert abs(beats.size - study.beats_analyzed_samples / PERIOD) <= 2


async def test_reanalizar_no_duplica_latidos(client, s3, db, make_patient, make_device) -> None:
    study = await _ingest(client, db, make_patient, make_device, seconds=60)
    before = load_beats(study).copy()

    await append_beat_analysis(db, study)

    assert np.array_equal(load_beats(study), before)


async def test_al_cerrar_se_analiza_la_cola_y_se_compacta_sin_perder_latidos(
    client, s3, db, make_patient, make_device
) -> None:
    study = await _ingest(client, db, make_patient, make_device, seconds=60)
    study.status = StudyStatus.COMPLETED

    await append_beat_analysis(db, study, max_samples=None)
    beats = load_beats(study).copy()
    study.ecg_beat_chunks = compact_beat_chunks(study, force=True)

    assert study.beats_analyzed_samples == study.samples_count
    assert len(study.ecg_beat_chunks) == 1
    assert study.ecg_beat_chunks[0]["beatCount"] == beats.size
    assert np.array_equal(load_beats(study), beats)
    _assert_regular(beats)


async def test_el_tope_por_pasada_avanza_de_a_poco_sin_huecos(
    client, s3, db, make_patient, make_device, monkeypatch
) -> None:
    monkeypatch.setattr(processing, "BEAT_SAMPLES_PER_PASS", 10_000)
    study = await _ingest(client, db, make_patient, make_device, seconds=90)
    study.status = StudyStatus.COMPLETED
    passes = 0
    while study.beats_analyzed_samples < study.samples_count:
        previous = study.beats_analyzed_samples
        await append_beat_analysis(db, study, max_samples=10_000)
        assert study.beats_analyzed_samples - previous <= 10_000
        passes += 1

    assert passes >= 2
    _assert_regular(load_beats(study))
