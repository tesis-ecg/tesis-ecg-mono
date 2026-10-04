"""El análisis de latidos de las métricas Holter fuera del camino feliz.

`append_beat_analysis` (Pan-Tompkins detrás de `beats_analyzed_samples`) corre
en cada lote, en la misma transacción que el segmento, la Capa A y el motor de
detección. Sin guarda, una falla suya dejaba `FAILED` el lote entero —con su
Capa A y la pasada del motor— y, como su cursor no avanzaba, también cada lote
siguiente del estudio. Lo que fija este archivo:

- corre en su SAVEPOINT (`processing._guarded`), como el motor: si falla se
  deshace solo lo suyo y el próximo lote lo vuelve a intentar;
- un lock perdido adentro de cualquiera de las dos pasadas **no** se traga: es
  de la transacción entera, y `process_batch` deja el lote pendiente;
- la señal que ninguna corrida cubre (un estudio anterior a la línea de tiempo)
  se saltea en vez de clavar el cursor antes del hueco.

El camino del cierre (la finalización con los latidos rotos) está en
`test_ml_block_recovery`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent
from app.db.models.study import StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment
from app.ml.decompression import FLAG_LEAD_OFF
from app.modules.ingest import processing
from app.modules.ingest.processing import process_batch
from tests.ecg_synth import SAMPLE_RATE
from tests.ingest_helpers import build_frames, post_frames
from tests.test_ml_blocks import BLOCK, _calidad, _estudio, _mundo, _sinusal, bloques_cortos
from tests.test_ml_ingest import finalizar

__all__ = ["bloques_cortos"]

SR = SAMPLE_RATE


def _lock_perdido() -> DBAPIError:
    from asyncpg.exceptions import LockNotAvailableError

    return DBAPIError("SELECT ... FOR UPDATE", {}, LockNotAvailableError())


def _contendido(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise _lock_perdido()


def _roto(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise RuntimeError("Pan-Tompkins no soporta este caso")


async def _estados_de_lotes(db, study_id: uuid.UUID) -> list[ProcessingStatus]:  # type: ignore[no-untyped-def]
    lotes = await db.scalars(
        select(ECGBatch).where(ECGBatch.study_id == study_id).order_by(ECGBatch.first_seq)
    )
    return [lote.processing_status for lote in lotes.all()]


async def _tipos_capa_a(db, study_id: uuid.UUID) -> set[str]:  # type: ignore[no-untyped-def]
    eventos = await db.scalars(select(ECGEvent).where(ECGEvent.study_id == study_id))
    return {evento.event_metadata["kind"] for evento in eventos.all()}


# --------------------------------------------------------------------------- #
# La pasada guardada
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("ml_engine", "bloques_cortos")
async def test_una_falla_de_los_latidos_no_frena_el_lote_ni_la_capa_a_ni_el_motor(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """Pan-Tompkins falla en cada lote. Los lotes quedan `DONE` con su Capa A,
    el motor analiza su bloque en la misma transacción, y el cursor de latidos
    queda donde estaba. Arreglado, el lote siguiente retoma desde ahí."""
    real = processing.analyze_window
    monkeypatch.setattr(processing, "analyze_window", _roto)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    señal, flags = _sinusal(75.0)
    flags[5 * SR : 6 * SR] |= FLAG_LEAD_OFF

    for a, b in ((0, 15), (15, 30), (30, 45), (45, 60), (60, 75)):
        await chaleco.enviar(señal[a * SR : b * SR], flags[a * SR : b * SR])

    assert await _estados_de_lotes(db, study_id) == [ProcessingStatus.DONE] * 5
    assert "lead_off" in await _tipos_capa_a(db, study_id)
    study = await _estudio(db, study_id)
    assert study.samples_count == 75 * SR
    assert study.beats_analyzed_samples == 0
    assert study.ecg_beat_chunks == []
    # El bloque [0, 60 s) se completó con el último lote: el motor lo analizó
    # en la misma transacción en la que fallaron los latidos.
    assert study.ml_analyzed_samples == BLOCK
    assert await _calidad(db, study_id)

    monkeypatch.setattr(processing, "analyze_window", real)
    resto, resto_flags = _sinusal(15.0, seed=3)
    await chaleco.enviar(resto, resto_flags)

    study = await _estudio(db, study_id)
    assert study.beats_analyzed_samples == (
        study.samples_count - processing.BEAT_CONTEXT_SECONDS * SR
    )
    assert processing.load_beats(study).size > 0


async def test_un_lock_perdido_en_los_latidos_deja_el_lote_pendiente(
    client, s3, db, monkeypatch, make_patient, make_device
) -> None:
    """La guarda no se traga la contención: el lote no está roto, perdió la
    carrera. Queda pendiente para la próxima pasada, y nada de lo que ya había
    escrito (segmento, línea de tiempo) quedó a medias."""
    monkeypatch.setattr(processing, "analyze_window", _contendido)
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    # 45 s: con la cola de 30 s retenida, el lote ya tiene 15 s para analizar.
    body = (await post_frames(client, device, api_key, build_frames(45 * SR))).json()

    await process_batch(db, body["batchId"])

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    await db.refresh(batch)
    assert batch.processing_status is ProcessingStatus.PENDING
    assert batch.processing_error is None
    assert (await _estudio(db, body["studyId"])).samples_count == 0


@pytest.mark.usefixtures("ml_engine", "bloques_cortos")
async def test_un_lock_perdido_en_el_motor_deja_el_lote_pendiente(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    monkeypatch.setattr(processing, "analyze_batch", _contendido)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    # El rollback de `process_batch` expira el estudio de la sesión del test.
    study_id = study.id
    señal, flags = _sinusal(75.0)

    body = await chaleco.enviar(señal, flags)

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    await db.refresh(batch)
    assert batch.processing_status is ProcessingStatus.PENDING
    assert batch.processing_error is None
    study = await _estudio(db, study_id)
    assert study.samples_count == 0
    assert study.ml_analyzed_samples == 0


# --------------------------------------------------------------------------- #
# Señal sin corrida
# --------------------------------------------------------------------------- #


async def test_la_senal_sin_corrida_no_clava_el_cursor_de_latidos(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """Un estudio ingerido antes de la línea de tiempo, sin `backfill_timeline`:
    su primera corrida arranca en el `samples_count` que tenía. El cursor de
    latidos se quedaba antes del hueco y el informe en `BEAT_ANALYSIS_PENDING`
    para siempre. Ahora la saltea, como el motor: analiza la corrida mientras
    llega y, al cerrar, llega hasta el final."""
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    señal, flags = _sinusal(60.0)
    await chaleco.enviar(señal[: 15 * SR], flags[: 15 * SR])
    await db.execute(delete(StudyTimelineSegment).where(StudyTimelineSegment.study_id == study_id))

    for a, b in ((15, 30), (30, 45), (45, 60)):
        await chaleco.enviar(señal[a * SR : b * SR], flags[a * SR : b * SR])

    assert await _estados_de_lotes(db, study_id) == [ProcessingStatus.DONE] * 4
    study = await _estudio(db, study_id)
    # La corrida arranca a los 15 s; sus últimos 30 s esperan contexto.
    assert study.beats_analyzed_samples == 30 * SR

    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.status is StudyStatus.COMPLETED
    assert study.beats_analyzed_samples == study.samples_count == 60 * SR
    latidos = processing.load_beats(study)["sample_index"]
    assert latidos.size > 0
    assert int(latidos.min()) >= 15 * SR


async def test_backfill_timeline_vuelve_a_cero_los_latidos_que_salteo(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """El remedio documentado para la señal sin corrida es `backfill_timeline`,
    que le da corrida. Pero el cursor de latidos ya la dejó atrás: sin volverlo
    a cero, ese prefijo quedaba contado como tiempo analizado sin un solo latido
    y bajaba `averageBpm` sin aviso. El script los vuelve a cero cuando cambian
    los tramos, y la pasada siguiente rehace todo desde el principio."""
    from contextlib import asynccontextmanager

    from app.scripts import backfill_timeline

    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    señal, flags = _sinusal(45.0)
    await chaleco.enviar(señal[: 15 * SR], flags[: 15 * SR])
    await db.execute(delete(StudyTimelineSegment).where(StudyTimelineSegment.study_id == study_id))
    for a, b in ((15, 30), (30, 45)):
        await chaleco.enviar(señal[a * SR : b * SR], flags[a * SR : b * SR])
    await finalizar(db, monkeypatch, study_id)
    study = await _estudio(db, study_id)
    assert study.beats_analyzed_samples == study.samples_count
    assert int(processing.load_beats(study)["sample_index"].min()) >= 15 * SR

    @asynccontextmanager
    async def _misma_sesion():  # type: ignore[no-untyped-def]
        yield db

    monkeypatch.setattr(backfill_timeline, "async_session_factory", _misma_sesion)
    await backfill_timeline._run(dry_run=False, study_id=study_id)

    study = await _estudio(db, study_id)
    assert study.beats_analyzed_samples == 0
    assert study.ecg_beat_chunks == []

    await finalizar(db, monkeypatch, study_id, cerrar=False)

    study = await _estudio(db, study_id)
    assert study.beats_analyzed_samples == study.samples_count
    assert int(processing.load_beats(study)["sample_index"].min()) < 15 * SR

    # Correrlo otra vez no cambia los tramos: los latidos quedan como están.
    await backfill_timeline._run(dry_run=False, study_id=study_id)
    study = await _estudio(db, study_id)
    assert study.beats_analyzed_samples == study.samples_count
