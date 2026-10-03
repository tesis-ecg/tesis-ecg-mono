"""El motor por bloques fuera del camino feliz.

`test_ml_blocks` fija el contrato del cursor con un chaleco que sube parejo y un
médico que cierra cuando ya se procesó todo. Acá, lo que pasa en producción
cuando eso no se cumple: el chaleco deja de subir con la cola de la corrida a
medio bloque, el estudio se cierra con lotes todavía en cola, un estudio viejo
no tiene línea de tiempo desde la muestra cero, el motor falla, y la
recuperación desde el manifest no puede quedar agendando trabajo imposible en
cada vista.

Mismos bloques cortos (60 s con 30 s de contexto y 10 de contexto derecho) que
`test_ml_blocks`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent
from app.db.models.signal_quality import SignalQualityLevel
from app.db.models.study import StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment
from app.db.models.user import UserRole
from app.ml.decompression import FLAG_LEAD_OFF
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest import processing
from app.modules.ingest.processing import flush_stale_tails, process_batch, process_study_task
from tests.ecg_synth import SAMPLE_RATE, to_microvolts
from tests.frame_builder import Sample, encode_samples
from tests.ingest_helpers import STEP_MS, post_frames
from tests.test_ml_blocks import (
    BLOCK,
    _calidad,
    _Chaleco,
    _ecg,
    _espiar_motor,
    _estudio,
    _eventos_del_motor,
    _latidos,
    _mundo,
    _sinusal,
    bloques_cortos,
)
from tests.test_ml_ingest import finalizar

pytestmark = pytest.mark.usefixtures("ml_engine", "bloques_cortos")

__all__ = ["bloques_cortos"]

SR = SAMPLE_RATE


def _misma_sesion(monkeypatch: pytest.MonkeyPatch, db) -> None:
    """Las tareas de fondo abren su propia sesión: se la apunta a la del test."""

    @asynccontextmanager
    async def _sesion():
        yield db

    monkeypatch.setattr("app.db.session.async_session_factory", _sesion)


async def _enviar_en_lotes(chaleco: _Chaleco, señal, flags, cortes: tuple[int, ...]) -> None:
    for a, b in zip(cortes, cortes[1:], strict=False):
        await chaleco.enviar(señal[a * SR : b * SR], flags[a * SR : b * SR])


async def _estados_de_lotes(db, study_id: uuid.UUID) -> list[ProcessingStatus]:
    lotes = await db.scalars(
        select(ECGBatch).where(ECGBatch.study_id == study_id).order_by(ECGBatch.first_seq)
    )
    return [lote.processing_status for lote in lotes.all()]


def _avisos_de(sent_pushes, kind: str) -> int:
    return len([item for item in sent_pushes if item[1].data.get("kind") == kind])


# --------------------------------------------------------------------------- #
# La cola de una corrida que dejó de crecer
# --------------------------------------------------------------------------- #


async def test_la_cola_de_una_corrida_que_dejo_de_crecer_se_analiza_sin_esperar_otro_lote(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """55 s con una pausa a los 38,5 s y el chaleco deja de subir (se fue de la
    casa, se cortó el router). Un bloque son 60 s: la pausa ya llegó pero queda
    en la cola de la corrida abierta, esperando un lote que no viene. Pasados
    `ml_open_tail_flush_minutes` sin lotes, el barrido la analiza como cerrada
    y avisa. Cuando la corrida sigue, el bloque siguiente arranca en el cursor
    con su contexto y la pausa no se duplica ni vuelve a avisar."""
    latidos = sorted([t for t in _latidos([(55.0, 60.0)]) if not 38.6 < t < 41.6] + [41.4])
    señal, flags = _ecg(latidos, 55.0)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    await _enviar_en_lotes(chaleco, señal, flags, (0, 15, 30, 45, 55))

    assert (await _estudio(db, study_id)).ml_analyzed_samples == 0
    assert await _eventos_del_motor(db, study_id, "pause") == []

    _misma_sesion(monkeypatch, db)
    # El `client` cambia la tarea del módulo por un espía; el barrido usa la real.
    monkeypatch.setattr(processing, "process_study_task", process_study_task)
    # Recién subidos, no es una cola vieja: el lote siguiente la puede completar.
    assert await flush_stale_tails() == []
    mas_tarde = datetime.now(UTC) + timedelta(minutes=settings.ml_open_tail_flush_minutes + 1)
    assert await flush_stale_tails(now=mas_tarde) == [study_id]

    study = await _estudio(db, study_id)
    assert study.status is StudyStatus.IN_PROGRESS
    assert study.ml_analyzed_samples == study.samples_count == 55 * SR
    (pausa,) = await _eventos_del_motor(db, study_id, "pause")
    assert pausa.event_metadata["pauseSeconds"] == pytest.approx(2.9, abs=0.05)
    assert _avisos_de(sent_pushes, "pause") == 1
    # Ya analizada, el barrido no la vuelve a mirar.
    assert await flush_stale_tails(now=mas_tarde) == []

    # La corrida sigue: un bloque entero desde el cursor. (El commit del barrido
    # expiró el equipo de la sesión del test.)
    await db.refresh(chaleco.device)
    resto, resto_flags = _sinusal(70.0, seed=5)
    await chaleco.enviar(resto, resto_flags)
    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == 55 * SR + BLOCK
    (pausa,) = await _eventos_del_motor(db, study_id, "pause")
    assert _avisos_de(sent_pushes, "pause") == 1


async def test_un_lote_que_llego_y_no_se_proceso_lo_drena_el_barrido(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """El barrido ya analizó la cola (cursor al día) y llega un lote más, con
    una pausa, que nunca se procesa: su tarea se perdió en un reinicio, o no
    consiguió la fila. No está en `samples_count`, así que el estudio no parecía
    tener nada pendiente y la pausa esperaba a que el equipo volviera a subir.
    El barrido también busca lotes sin procesar: lo drena, lo analiza y avisa."""
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    señal, flags = _sinusal(55.0)
    await _enviar_en_lotes(chaleco, señal, flags, (0, 15, 30, 45, 55))
    _misma_sesion(monkeypatch, db)
    monkeypatch.setattr(processing, "process_study_task", process_study_task)
    ahora = datetime.now(UTC)
    vieja = ahora + timedelta(minutes=settings.ml_open_tail_flush_minutes + 1)
    assert await flush_stale_tails(now=vieja) == [study_id]
    assert (await _estudio(db, study_id)).ml_analyzed_samples == 55 * SR

    await db.refresh(chaleco.device)
    latidos = sorted([t for t in _latidos([(14.0, 60.0)]) if not 5.6 < t < 8.6] + [8.4])
    pausa_señal, pausa_flags = _ecg(latidos, 14.0)
    await chaleco.enviar(pausa_señal, pausa_flags, procesar=False)
    assert await _estados_de_lotes(db, study_id) == [ProcessingStatus.DONE] * 4 + [
        ProcessingStatus.PENDING
    ]

    mas_tarde = vieja + timedelta(minutes=settings.ml_open_tail_flush_minutes + 1)
    assert await flush_stale_tails(now=mas_tarde) == [study_id]

    study = await _estudio(db, study_id)
    assert await _estados_de_lotes(db, study_id) == [ProcessingStatus.DONE] * 5
    assert study.ml_analyzed_samples == study.samples_count == 69 * SR
    (pausa,) = await _eventos_del_motor(db, study_id, "pause")
    assert pausa.event_metadata["pauseSeconds"] == pytest.approx(2.9, abs=0.05)
    assert _avisos_de(sent_pushes, "pause") == 1
    # Drenado, ya no hay nada que barrer.
    assert await flush_stale_tails(now=mas_tarde) == []


async def test_un_hallazgo_viejo_se_escribe_con_su_alerta_pero_no_despierta_al_paciente(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """Con la antigüedad máxima del push casi en cero, la misma pausa de un
    estudio en vivo es "de hace demasiado": queda para el médico, sin push."""
    monkeypatch.setattr(settings, "ml_push_max_age_minutes", 1e-4)
    latidos = sorted([t for t in _latidos([(75.0, 60.0)]) if not 38.6 < t < 41.6] + [41.4])
    señal, flags = _ecg(latidos, 75.0)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)

    await chaleco.enviar(señal, flags)

    (pausa,) = await _eventos_del_motor(db, study.id, "pause")
    alertas = (await db.scalars(select(Alert).where(Alert.event_id == pausa.id))).all()
    assert len(alertas) == 1
    assert _avisos_de(sent_pushes, "pause") == 0


async def test_el_backlog_de_un_arranque_anterior_avisa_al_medico_pero_no_al_paciente(
    client, s3, db, sent_pushes, make_patient, make_device, make_study
) -> None:
    """Flash conserva lo no confirmado a través de un reinicio, y después de un
    cambio de batería el puente sube backlog de un `bootId` que ya no es el
    actual. Su hora se ancla a cuando llegó, no a cuando se grabó
    (`ingest_service`): la pausa parecía de recién y despertaba al paciente,
    aunque fuera de hace horas. Sin hora propia, queda para el médico."""
    latidos = sorted([t for t in _latidos([(75.0, 60.0)]) if not 38.6 < t < 41.6] + [41.4])
    señal, flags = _ecg(latidos, 75.0)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    tramas = encode_samples(
        [
            Sample(timestamp_ms=index * STEP_MS, raw_uV=[value], flags=int(flag))
            for index, (value, flag) in enumerate(zip(to_microvolts(señal), flags, strict=True))
        ],
        first_seq=0,
        boot_id=0,
        simulated=True,
    )
    # El equipo ya arrancó de nuevo: el ancla del puente es del arranque 1.
    body = (await post_frames(client, chaleco.device, chaleco.api_key, tramas, boot_id=1)).json()
    await process_batch(db, body["batchId"])

    (corrida,) = await repo.list_timeline_segments(db, study.id)
    assert corrida.anchor_matches_boot is False
    (pausa,) = await _eventos_del_motor(db, study.id, "pause")
    alertas = (await db.scalars(select(Alert).where(Alert.event_id == pausa.id))).all()
    assert len(alertas) == 1
    assert _avisos_de(sent_pushes, "pause") == 0


# --------------------------------------------------------------------------- #
# Cerrar con lotes en cola
# --------------------------------------------------------------------------- #


async def test_cerrar_con_lotes_en_cola_analiza_la_cola_una_sola_vez(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """El médico cierra mientras el chaleco sube, o el rebobinado de `seq` cierra
    solo: los lotes que quedaron en cola se drenan después del cierre. Si cada
    uno analizara su propia "cola", el análisis volvería a ser por lote —y una
    cola de 1 s salía BAD/psqi—. Tiene que dar lo mismo que si se hubieran
    procesado antes de cerrar: una sola cola, analizada al final."""
    llamadas = _espiar_motor(monkeypatch)
    señal, flags = _sinusal(40.0)
    en_cola = (25, 35, 39, 40)

    async def ingerir(procesar: bool) -> tuple[uuid.UUID, list[tuple[int, int, int]]]:
        llamadas.clear()
        chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
        study_id = study.id
        await chaleco.enviar(señal[: 25 * SR], flags[: 25 * SR])
        for a, b in zip(en_cola, en_cola[1:], strict=False):
            await chaleco.enviar(señal[a * SR : b * SR], flags[a * SR : b * SR], procesar=procesar)
        await finalizar(db, monkeypatch, study_id)
        assert set(await _estados_de_lotes(db, study_id)) == {ProcessingStatus.DONE}
        return study_id, [(c["start_sample_index"], c["n"], c["context_samples"]) for c in llamadas]

    en_vivo, motor_en_vivo = await ingerir(procesar=True)
    encolado, motor_encolado = await ingerir(procesar=False)

    assert motor_encolado == motor_en_vivo == [(0, 40 * SR, 0)]

    def calidad(filas) -> list[tuple[int, int, SignalQualityLevel]]:
        return [(f.start_sample_index, f.sample_count, f.level) for f in filas]

    assert calidad(await _calidad(db, encolado)) == calidad(await _calidad(db, en_vivo))
    assert calidad(await _calidad(db, encolado)) == [(0, 40 * SR, SignalQualityLevel.GOOD)]
    assert await _eventos_del_motor(db, encolado, "noise_burst") == []


# --------------------------------------------------------------------------- #
# Estudios viejos y fallas del motor
# --------------------------------------------------------------------------- #


async def test_una_linea_de_tiempo_que_no_arranca_en_cero_no_frena_la_ingesta(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """Un estudio ingerido antes de la línea de tiempo, sin `backfill_timeline`:
    su primera corrida arranca en el `samples_count` que tenía. Esa señal no se
    puede analizar por corrida y se saltea. Antes el motor lo trataba como
    imposible y levantaba un error en cada lote: sin Capa A, sin visor, sin
    vista filtrada para el resto del estudio."""
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    señal, flags = _sinusal(45.0)
    await chaleco.enviar(señal[: 15 * SR], flags[: 15 * SR])
    await db.execute(delete(StudyTimelineSegment).where(StudyTimelineSegment.study_id == study_id))

    await _enviar_en_lotes(chaleco, señal, flags, (15, 30, 45))

    assert await _estados_de_lotes(db, study_id) == [ProcessingStatus.DONE] * 3
    assert (await _estudio(db, study_id)).samples_count == 45 * SR

    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.samples_count
    assert [fila.start_sample_index for fila in await _calidad(db, study_id)] == [15 * SR]


async def test_una_falla_del_motor_no_frena_la_ingesta_ni_el_cierre(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """El motor corre en un SAVEPOINT: si falla, se deshace solo lo suyo. El lote
    queda `DONE` con su Capa A, el cierre completa la vista filtrada, y el
    cursor queda donde estaba para que la próxima pasada lo vuelva a intentar."""
    real = processing.analyze_batch

    def _roto(*args, **kwargs):
        raise RuntimeError("el motor no soporta este caso")

    monkeypatch.setattr(processing, "analyze_batch", _roto)
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    study = await make_study(patient, device, filter_view_enabled=True)
    study_id = study.id
    chaleco = _Chaleco(client, db, device, api_key)
    señal, flags = _sinusal(75.0)
    flags[5 * SR : 6 * SR] |= FLAG_LEAD_OFF

    await _enviar_en_lotes(chaleco, señal, flags, (0, 15, 30, 45, 60, 75))

    assert set(await _estados_de_lotes(db, study_id)) == {ProcessingStatus.DONE}
    capa_a = (await db.scalars(select(ECGEvent).where(ECGEvent.study_id == study_id))).all()
    assert "lead_off" in {evento.event_metadata["kind"] for evento in capa_a}
    assert (await _estudio(db, study_id)).ml_analyzed_samples == 0

    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.filtered_samples_count == study.samples_count == 75 * SR
    assert study.ml_analyzed_samples == 0

    # Arreglado el motor, la recuperación (el manifest agenda la misma tarea)
    # analiza lo que quedó.
    monkeypatch.setattr(processing, "analyze_batch", real)
    await process_study_task(study_id)
    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.samples_count
    assert await _calidad(db, study_id)


# --------------------------------------------------------------------------- #
# Recuperación desde el manifest
# --------------------------------------------------------------------------- #


async def test_el_manifest_no_reagenda_estudios_que_el_motor_no_puede_avanzar(
    client,
    s3,
    db,
    monkeypatch,
    scheduled_batches,
    as_user,
    make_user,
    make_patient,
    make_device,
    make_study,
) -> None:
    """La recuperación del manifest agenda la finalización de un estudio cerrado
    cuyo cursor no llegó al final. Un estudio seedeado tiene `samples_count` sin
    señal que el motor pueda leer, y uno sin línea de tiempo tiene señal sin
    corridas: ninguno puede avanzar el cursor por análisis. No pueden quedar
    tomando la fila del estudio en cada vista."""
    as_user(await make_user(UserRole.ADMIN))
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    seedeado = await make_study(
        patient, device, status=StudyStatus.COMPLETED, samples_count=30 * SR
    )
    seedeado_id = seedeado.id
    assert (await client.get(f"/studies/{seedeado_id}/ecg/manifest")).status_code == 200
    assert seedeado_id not in scheduled_batches

    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    señal, flags = _sinusal(30.0)
    await chaleco.enviar(señal, flags)
    await db.execute(delete(StudyTimelineSegment).where(StudyTimelineSegment.study_id == study_id))
    await finalizar(db, monkeypatch, study_id)
    study = await _estudio(db, study_id)
    assert study.status is StudyStatus.COMPLETED
    assert study.ml_analyzed_samples == study.samples_count

    scheduled_batches.clear()
    # Un usuario nuevo: el commit de la finalización expiró el anterior.
    as_user(await make_user(UserRole.ADMIN))
    assert (await client.get(f"/studies/{study_id}/ecg/manifest")).status_code == 200
    assert study_id not in scheduled_batches


def test_el_arranque_lanza_el_barrido_de_colas_viejas_y_el_apagado_lo_corta(monkeypatch) -> None:
    """El barrido vive en el `lifespan`: arranca con la API (importando el motor
    en un hilo, no al cargar `app.main`) y se cancela limpio al apagarla."""
    import asyncio
    import time

    from fastapi.testclient import TestClient

    from app.main import app

    vueltas: list[str] = []

    async def _barrido() -> None:
        vueltas.append("arrancó")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            vueltas.append("cancelado")
            raise

    monkeypatch.setattr(processing, "sweep_stale_tails_forever", _barrido)
    with TestClient(app):
        limite = time.monotonic() + 30
        while not vueltas and time.monotonic() < limite:
            time.sleep(0.05)
        assert vueltas == ["arrancó"]
    assert vueltas == ["arrancó", "cancelado"]
