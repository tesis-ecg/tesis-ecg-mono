"""El motor enganchado a la ingesta real, de la trama al `ecg_event`.

Estos tests recorren el camino de producción entero: `POST /ingest/ecg-frames`
con tramas Rice de 256 B → `process_batch` → decodificación → gate de calidad →
banco de plantillas → hallazgos, calidad y alertas en la base.

Todo lo que se ingiere es **ECG con morfología real** (`tests/ecg_synth.py`), no
la onda de prueba del codec: el motor mide si dos formas se parecen, y sobre una
onda cuadrada esa pregunta no significa nada.

El motor analiza por **bloques** de la corrida (`ml_analysis_block_seconds`,
300 s) y no por lote. Un registro de 930 s son tres bloques enteros más los
30 s de contexto derecho que el tercero espera con la corrida abierta
(`ml_analysis_lookahead_seconds`), y se analiza con el estudio abierto; uno de
120 s es la cola de una corrida abierta y espera al cierre, así que esos tests
cierran el estudio y corren la finalización real (`finalizar`).
"""

from dataclasses import replace

import numpy as np
import pytest
from sqlalchemy import select

from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventType
from app.db.models.signal_quality import SignalQualityInterval, SignalQualityLevel
from app.ml.decompression import FLAG_LEAD_OFF
from app.ml.pipeline import PIPELINE_VERSION
from app.modules.ingest import ml_persistence
from app.modules.ingest.processing import process_batch, process_study_task
from app.modules.patient_app.notifications_service import anomaly_title
from tests.ecg_synth import SAMPLE_RATE, synth_ecg, to_microvolts
from tests.frame_builder import Sample, encode_samples
from tests.ingest_helpers import STEP_MS, post_frames

pytestmark = pytest.mark.usefixtures("ml_engine")


def _frames(signal_mv: np.ndarray, flags: np.ndarray, *, first_seq: int = 0) -> list[bytes]:
    microvolts = to_microvolts(signal_mv)
    samples = [
        Sample(timestamp_ms=index * STEP_MS, raw_uV=[value], flags=int(flag))
        for index, (value, flag) in enumerate(zip(microvolts, flags, strict=True))
    ]
    return encode_samples(samples, first_seq=first_seq, boot_id=0, simulated=True)


async def _ingest(client, db, device, api_key, frames):
    body = (await post_frames(client, device, api_key, frames)).json()
    await process_batch(db, body["batchId"])
    return body


async def finalizar(db, monkeypatch, study_id, *, cerrar: bool = True) -> None:
    """Cierra el estudio por el servicio real y corre la finalización con esta sesión.

    `process_study_task` abre su propia sesión contra el engine global, que no
    vería nada de lo que escribió el test: se la apunta a la sesión del test.
    `process_study_task` se importa al cargar el módulo, antes de que el fixture
    `scheduled_batches` (del `client`) reemplace el atributo del módulo por un
    espía.
    """
    from contextlib import asynccontextmanager

    from app.modules.studies import studies_service
    from app.modules.studies.studies_schemas import StudyIdInput

    if cerrar:
        await studies_service.complete_study(
            StudyIdInput(doctor_id=None, study_id=study_id, actor_id=None), db
        )

    @asynccontextmanager
    async def _misma_sesion():
        yield db

    monkeypatch.setattr("app.db.session.async_session_factory", _misma_sesion)
    await process_study_task(study_id)


async def _events(db, study_id) -> list[ECGEvent]:
    result = await db.scalars(
        select(ECGEvent)
        .where(ECGEvent.study_id == study_id, ECGEvent.deleted_at.is_(None))
        .order_by(ECGEvent.timestamp_in_recording)
    )
    return list(result.all())


def _kinds(events: list[ECGEvent]) -> set[str]:
    return {event.event_metadata["kind"] for event in events if event.event_metadata}


async def _world(make_patient, make_device, make_study):
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    study = await make_study(patient, device)
    return patient, device, api_key, study


async def test_un_foco_ectopico_ingerido_produce_hallazgos_de_morfologia(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """El camino completo, de la trama comprimida al hallazgo agrupado."""
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=930.0, ectopic_every=12)

    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))

    events = await _events(db, study.id)
    kinds = _kinds(events)
    assert "recurrent_morphology" in kinds
    assert "morphology_anomaly" in kinds

    header = next(e for e in events if e.event_metadata["kind"] == "recurrent_morphology")
    assert header.event_type is ECGEventType.ANOMALY
    assert header.model_version == PIPELINE_VERSION
    assert header.dedupe_key.startswith("cluster:")
    assert header.event_metadata["beatCount"] == pytest.approx(len(signal.ectopic_peaks), rel=0.1)
    assert header.event_metadata["source"] == "ml"
    assert header.event_metadata["scope"] == "study"


async def test_la_calidad_se_persiste_como_intervalos_y_no_como_eventos(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """Dos minutos limpios son UNA fila, no 12. La calidad es una propiedad
    continua del registro, no un hallazgo puntual."""
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=120.0)

    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    await finalizar(db, monkeypatch, study.id)

    intervals = list(
        (
            await db.scalars(
                select(SignalQualityInterval).where(SignalQualityInterval.study_id == study.id)
            )
        ).all()
    )
    assert len(intervals) == 1
    assert intervals[0].level is SignalQualityLevel.GOOD
    assert intervals[0].window_count == 12
    assert intervals[0].model_version == PIPELINE_VERSION
    assert intervals[0].metrics["bsqi"] == pytest.approx(1.0)


async def test_el_electrodo_despegado_parte_la_calidad_en_tres_tramos(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=120.0)
    flags = signal.flags.copy()
    flags[60 * SAMPLE_RATE : 70 * SAMPLE_RATE] |= FLAG_LEAD_OFF

    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, flags))
    await finalizar(db, monkeypatch, study.id)

    intervals = list(
        (
            await db.scalars(
                select(SignalQualityInterval)
                .where(SignalQualityInterval.study_id == study.id)
                .order_by(SignalQualityInterval.start_sample_index)
            )
        ).all()
    )
    assert [item.level for item in intervals] == [
        SignalQualityLevel.GOOD,
        SignalQualityLevel.BAD,
        SignalQualityLevel.GOOD,
    ]
    assert intervals[1].reason == "lead_off"
    # Y la Capa A escribió su propio `lead_off` sobre la traza.
    assert "lead_off" in _kinds(await _events(db, study.id))


async def test_un_reintento_despues_de_una_falla_no_duplica_ni_infla_el_banco(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """La garantía que hace que un reintento sea seguro.

    Un lote `DONE` no se vuelve a procesar; el único reproceso real es el de un
    lote que falló. La falla se fuerza **después** de que el motor escribió sus
    hallazgos, su calidad y su banco: el rollback tiene que llevarse todo junto,
    y el reintento tiene que dejar exactamente lo mismo que una pasada limpia —
    mismas claves y mismos latidos en el banco. Contar dos veces los latidos de
    un lote falsearía la carga (`burdenPct`) que el médico lee como "el 8 % de
    tus latidos".
    """
    signal = synth_ecg(duration_s=930.0, ectopic_every=12)

    # Referencia: el mismo lote en un estudio que nunca falló.
    _, device, api_key, limpio = await _world(make_patient, make_device, make_study)
    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    # Valores planos: el rollback de más abajo expira las filas ORM cargadas.
    referencia = [event.dedupe_key for event in await _events(db, limpio.id)]
    await db.refresh(limpio)
    vistos = limpio.ml_state["beatsSeen"]
    assert vistos > 0

    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    # El rollback expira los objetos de la sesión: el id se guarda antes, porque
    # leer `study.id` después dispararía una carga síncrona.
    study_id = study.id
    body = (
        await post_frames(client, device, api_key, _frames(signal.signal_mv, signal.flags))
    ).json()

    real_recount = ml_persistence.recount_events
    llamadas = {"n": 0}

    async def _falla_una_vez(session, target) -> None:
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("corte después de escribir los hallazgos")
        await real_recount(session, target)

    monkeypatch.setattr(ml_persistence, "recount_events", _falla_una_vez)
    await process_batch(db, body["batchId"])

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    await db.refresh(batch)
    assert batch.processing_status is ProcessingStatus.FAILED
    assert await _events(db, study_id) == []
    assert (
        await db.scalars(
            select(SignalQualityInterval).where(SignalQualityInterval.study_id == study_id)
        )
    ).all() == []
    await db.refresh(study)
    assert not (study.ml_state or {}).get("beatsSeen")

    await process_batch(db, body["batchId"])

    await db.refresh(batch)
    assert batch.processing_status is ProcessingStatus.DONE
    eventos = await _events(db, study_id)
    assert len(eventos) == len(referencia)
    assert {e.dedupe_key for e in eventos} == set(referencia)
    await db.refresh(study)
    assert study.ml_state["beatsSeen"] == vistos


async def test_events_count_cuenta_cada_evento_una_sola_vez(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """Dos escritores y un upsert: `events_count` sale de contar filas.

    El encabezado por morfología se upsertea en cada lote —la fila ya existe y
    solo crece su conteo—, así que un `+= escritos` lo contaría una vez por lote.
    """
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=930.0, ectopic_every=12)
    frames = _frames(signal.signal_mv, signal.flags)
    mitad = len(frames) // 2

    await _ingest(client, db, device, api_key, frames[:mitad])
    await _ingest(client, db, device, api_key, frames[mitad:])

    eventos = await _events(db, study.id)
    assert "recurrent_morphology" in _kinds(eventos)
    await db.refresh(study)
    assert study.events_count == len(eventos)


async def test_una_pausa_del_motor_notifica_con_su_kind(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """El aviso de un hallazgo del motor es el mismo `Pushable` que el de la Capa A.

    Con su `kind`, el título nombra lo que pasó ("una pausa en el ritmo") y el
    formulario que abre lo encabeza, en vez del aviso genérico.
    """
    patient, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=120.0)
    señal = signal.signal_mv.copy()
    flags = signal.flags.copy()
    # Una pausa de 2,6 s: genera hallazgo con alerta. Queda la línea de base con
    # su ruido; en cero exacto sería un riel (`quiet_gap.rail_mask`).
    inicio = 60 * SAMPLE_RATE
    largo = int(2.6 * SAMPLE_RATE)
    ruido = 0.008 * np.random.default_rng(5).standard_normal(largo)
    señal[inicio : inicio + largo] = (signal.signal_mv[0] + ruido).astype(señal.dtype)
    flags[inicio : inicio + largo] = 0

    await _ingest(client, db, device, api_key, _frames(señal, flags))
    # La finalización hace rollback de la sesión antes de tomar la fila, y eso
    # expira los objetos cargados: los ids se leen antes.
    patient_id, study_id = patient.id, study.id
    # Dos minutos son la cola de la corrida: el motor los analiza al cierre, y
    # el aviso sale de la finalización después de su commit.
    await finalizar(db, monkeypatch, study_id)

    pausa = next(e for e in await _events(db, study_id) if e.event_metadata["kind"] == "pause")
    alerta = (await db.scalars(select(Alert).where(Alert.event_id == pausa.id))).one()
    assert alerta.kind == "pause"
    avisos = [item for item in sent_pushes if item[1].data.get("type") == "report_request"]
    assert len(avisos) == 1
    paciente, mensaje = avisos[0]
    assert paciente == patient_id
    assert mensaje.data["alertId"] == str(alerta.id)
    assert mensaje.data["kind"] == "pause"
    assert mensaje.title == anomaly_title("pause")


async def test_con_el_motor_apagado_la_ingesta_sigue_funcionando(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """`ml_enabled=False` tiene que dejar el sistema exactamente como antes del
    motor: es la palanca para apagarlo en producción sin desplegar código."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ml_enabled", False)
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=120.0)
    flags = signal.flags.copy()
    flags[10 * SAMPLE_RATE : 20 * SAMPLE_RATE] |= FLAG_LEAD_OFF

    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, flags))

    events = await _events(db, study.id)
    # La Capa A —los bits del hardware— sigue escribiendo.
    assert "lead_off" in _kinds(events)
    # El motor, no.
    assert "morphology_anomaly" not in _kinds(events)
    assert (
        list(
            (
                await db.scalars(
                    select(SignalQualityInterval).where(SignalQualityInterval.study_id == study.id)
                )
            ).all()
        )
        == []
    )


async def test_los_hallazgos_del_motor_se_distinguen_de_los_manuales(
    client, s3, db, as_user, make_user, make_patient, make_device, make_study
) -> None:
    """`model_version IS NOT NULL` es el único predicado que dice "esto lo
    escribió el motor y se puede reescribir".

    Los tres escritores en el mismo estudio: el motor, la Capa A (los bits del
    hardware, que escribe la ingesta) y un hallazgo manual de `simulate-anomaly`.
    """
    from app.db.models.user import UserRole

    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=930.0, ectopic_every=12)
    flags = signal.flags.copy()
    # Un tramo de electrodo despegado: sin él la Capa A no escribe nada y las
    # afirmaciones sobre ella pasarían en vacío.
    flags[100 * SAMPLE_RATE : 110 * SAMPLE_RATE] |= FLAG_LEAD_OFF
    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, flags))
    admin = as_user(await make_user(role=UserRole.ADMIN))
    response = await admin.post(
        f"/studies/{study.id}/simulate-anomaly",
        json={"eventType": "pause", "secondsBeforeEnd": 30},
    )
    assert response.status_code == 200, response.text

    events = await _events(db, study.id)
    sources = [(event.event_metadata or {}).get("source") for event in events]
    motor = [event for event, source in zip(events, sources, strict=True) if source == "ml"]
    assert motor
    assert all(event.model_version == PIPELINE_VERSION for event in motor)
    assert all(event.dedupe_key is not None for event in motor)
    assert all(event.study_id == study.id for event in events)
    # La Capa A la escribe la ingesta y no el motor: sin versión de modelo, y
    # nada que la reescriba.
    hardware = [
        event for event, source in zip(events, sources, strict=True) if source == "firmware_flags"
    ]
    assert "lead_off" in _kinds(hardware)
    assert all(event.model_version is None for event in hardware)
    manual = [event for event in events if (event.event_metadata or {}).get("simulated")]
    assert len(manual) == 1
    assert manual[0].model_version is None
    assert len(motor) + len(hardware) + len(manual) == len(events)


async def test_persistir_dos_veces_el_mismo_resultado_no_duplica_ni_vuelve_a_avisar(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """La red de seguridad, sin el rollback de por medio.

    Hoy el cursor no vuelve a analizar un bloque y uno que falla no deja nada,
    así que este camino no se recorre en producción. Pero es la garantía que
    tiene que sostener cualquier escritura repetida de un mismo hallazgo: la
    segunda pausa toca a la primera, así que se empalma sobre ella —no se
    duplica— y no avisa de nuevo, porque el evento ya tiene su alerta.
    """
    from app.core.config import settings
    from app.db.models.ecg_event import ECGEventSeverity
    from app.ml.contracts import Finding
    from app.ml.pipeline import PipelineResult, build_config, empty_bank

    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=60.0)
    body = await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    pausa = Finding(
        kind="pause",
        event_type=ECGEventType.PAUSE,
        severity=ECGEventSeverity.HIGH,
        start_sample=30 * SAMPLE_RATE,
        length_samples=int(2.6 * SAMPLE_RATE),
        dedupe_key=f"pause:{30 * SAMPLE_RATE}",
        alert_message="Se detectó una pausa en el ritmo.",
    )
    resultado = PipelineResult(
        quality_intervals=(),
        findings=(pausa,),
        bank=empty_bank(build_config(settings, SAMPLE_RATE)),
        metrics={},
        model_version=PIPELINE_VERSION,
    )
    antes = len(await _events(db, study.id))
    alertas_antes = len((await db.scalars(select(Alert))).all())

    scope = ml_persistence.BlockScope(
        batch_id=batch.id,
        boot_id=batch.boot_id,
        run_start=0,
        read_start=0,
        block_start=0,
        block_end=60 * SAMPLE_RATE,
    )
    _, primero = await ml_persistence.persist_analysis(db, study, resultado, SAMPLE_RATE, scope)
    _, segundo = await ml_persistence.persist_analysis(db, study, resultado, SAMPLE_RATE, scope)

    assert primero is not None
    assert segundo is None
    assert len(await _events(db, study.id)) == antes + 1
    assert len((await db.scalars(select(Alert))).all()) == alertas_antes + 1

    # La red de abajo del empalme: si la búsqueda de candidatos no encuentra la
    # fila —acá, una corrida que arranca después de la pausa—, el `INSERT ... ON
    # CONFLICT DO NOTHING` por `dedupe_key` no la duplica, y como no la insertó
    # (sin `RETURNING`) tampoco crea una segunda alerta.
    sin_candidatos = replace(scope, run_start=40 * SAMPLE_RATE, read_start=40 * SAMPLE_RATE)
    escritas, tercero = await ml_persistence.persist_analysis(
        db, study, resultado, SAMPLE_RATE, sin_candidatos
    )
    assert (escritas, tercero) == (0, None)
    assert len(await _events(db, study.id)) == antes + 1
    assert len((await db.scalars(select(Alert))).all()) == alertas_antes + 1


async def test_la_finalizacion_funde_las_morfologias_de_un_estudio_completado(
    s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    """La fusión corre en `process_study_task` y no en el endpoint de cierre.

    Así la ve cualquier camino que complete el estudio —el médico, la
    desasignación del equipo, el rebobinado de `seq` de la ingesta, que cierra
    sin pasar por el servicio de estudios— y la ve después de drenar los lotes
    que quedaron en cola.
    """
    from contextlib import asynccontextmanager

    from app.db.models.ecg_event import ECGEventSeverity
    from app.db.models.study import StudyStatus
    from app.ml.morphology import Template, TemplateBank, beat_length
    from app.modules.ingest.processing import process_study_task
    from tests.test_studies_simulate_anomaly import _batch

    _, device, _, study = await _world(make_patient, make_device, make_study)
    batch = await _batch(db, device, study, SAMPLE_RATE * 60)
    largo = beat_length(SAMPLE_RATE)
    rng = np.random.default_rng(7)
    base = rng.standard_normal(largo).astype(np.float32)
    base /= np.linalg.norm(base)
    # La misma forma con un poco de ruido: dos plantillas donde había una.
    gemela = base + 0.02 * rng.standard_normal(largo).astype(np.float32)
    gemela /= np.linalg.norm(gemela)
    bank = TemplateBank(
        model_version=PIPELINE_VERSION,
        beat_length=largo,
        templates=(
            Template(0, base, 90, 85.0, 0, 20_000),
            Template(1, gemela, 10, 9.5, 5_000, 25_000),
        ),
        beats_seen=100,
        next_cluster_id=2,
    )
    # Totales de un bloque ya analizado: la fusión no analiza señal nueva y no
    # los puede perder (`bank_to_state` arma el estado desde cero).
    totales = {"analyzedSamples": 30_000.0, "windows": 6.0, "windowsGood": 6.0}
    ml_persistence.store_bank(study, bank, 0, totales)

    def _evento(dedupe_key: str, scope: str, cluster_id: int) -> ECGEvent:
        return ECGEvent(
            batch_id=batch.id,
            study_id=study.id,
            event_type=ECGEventType.ANOMALY,
            severity=ECGEventSeverity.LOW,
            timestamp_in_recording=10.0,
            event_metadata={
                "kind": "recurrent_morphology",
                "scope": scope,
                "clusterId": cluster_id,
            },
            model_version=PIPELINE_VERSION,
            dedupe_key=dedupe_key,
        )

    db.add_all(
        [
            _evento("cluster:0", "study", 0),
            _evento("cluster:1", "study", 1),
            _evento("morphology_anomaly:6000", "batch", 1),
        ]
    )
    # Como lo deja `ingest_service._recover_from_seq_rewind`: COMPLETED sin
    # pasar por `studies_service`.
    study.status = StudyStatus.COMPLETED
    await db.commit()

    @asynccontextmanager
    async def _misma_sesion():
        yield db

    monkeypatch.setattr("app.db.session.async_session_factory", _misma_sesion)
    await process_study_task(study.id)

    eventos = {event.dedupe_key: event for event in await _events(db, study.id)}
    assert set(eventos) == {"cluster:0", "morphology_anomaly:6000"}
    assert eventos["cluster:0"].event_metadata["beatCount"] == 100
    assert eventos["morphology_anomaly:6000"].event_metadata["clusterId"] == 0
    await db.refresh(study)
    assert study.events_count == 2
    assert study.ml_state["totals"] == totales
    assert study.ml_state["metrics"]["goodRatio"] == 1.0
    assert study.ml_state["metrics"]["beatsSeen"] == 100
    # Clave propia: la del último bloque la sigue referenciando la base hasta
    # que la fusión commitee.
    assert study.ml_state["templatesKey"].endswith(".merged.f32")
