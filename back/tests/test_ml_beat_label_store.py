"""Persistencia de la clasificación por latido y de los hallazgos nuevos.

La primera mitad prueba el almacén de etiquetas (`beat_label_store`) contra un S3
de mentira; la segunda, el camino de ingesta real: etiquetas en `ml_state`,
alertas con la regla de push, y que el motor no toque lo que el médico corrigió.
"""

import uuid
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import (
    ECGEventSeverity,
    ECGEventType,
    ECGEventValidation,
)
from app.ml import beat_labels as bl
from app.ml import morphology
from app.ml.contracts import Finding
from app.ml.pipeline import PIPELINE_VERSION, PipelineResult, build_config, empty_bank
from app.modules.ingest import beat_label_store, ml_persistence, processing
from tests.ecg_synth import SAMPLE_RATE, synth_conduction, synth_ecg
from tests.test_ml_ingest import _events, _frames, _ingest, _world, finalizar

pytestmark = pytest.mark.usefixtures("ml_engine")


def _records(start: int, count: int) -> bl.BeatLabelRecords:
    index = start + np.arange(count, dtype=np.int64) * 400
    return bl.build_records(
        index,
        np.full(count, bl.CODE_N, dtype=np.uint8),
        np.zeros(count, dtype=np.uint8),
        np.full(count, -20, dtype=np.int64),
        np.full(count, 25, dtype=np.int64),
        np.full(count, -90, dtype=np.int64),
        np.full(count, 1.1, dtype=np.float32),
        qrs_measured=np.ones(count, dtype=bool),
        p_detected=np.ones(count, dtype=bool),
    )


def _study(state: dict | None, cursor: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), ml_state=state, ml_analyzed_samples=cursor)


def _append(
    study_id: uuid.UUID, state: dict | None, block: int, size: int, *, count: int, before: int
):
    return beat_label_store.append_block(
        study_id,
        state,
        _records(block * size, count),
        block_start=block * size,
        block_end=(block + 1) * size,
        cursor_before=before,
    )


def test_las_etiquetas_se_guardan_por_bloque_y_se_leen_en_orden(s3) -> None:
    size = 150_000
    study = _study(None, 0)
    state = None
    for block in range(3):
        state = _append(
            study.id, state, block, size, count=0 if block == 1 else 5, before=block * size
        )
    study.ml_state = {beat_label_store.STATE_KEY: state}
    study.ml_analyzed_samples = 3 * size

    stream = beat_label_store.load_beat_labels(study)

    assert stream is not None
    assert (stream.from_sample, stream.until_sample) == (0, 3 * size)
    assert stream.classifier == bl.CLASSIFIER_VERSION
    assert stream.records.size == 10
    assert np.all(np.diff(stream.records["sample_index"]) > 0)
    # Hay chunk aunque el bloque no tenga latidos: así se verifica la cobertura.
    assert [chunk["beatCount"] for chunk in state["chunks"]] == [5, 0, 5]
    assert all(chunk["tier"] == 0 for chunk in state["chunks"])


def test_un_estudio_sin_etiquetas_devuelve_none(s3) -> None:
    assert beat_label_store.load_beat_labels(_study(None, 0)) is None
    assert beat_label_store.load_beat_labels(_study({"totals": {}}, 10)) is None


def test_los_niveles_acotan_la_lista_sin_perder_ni_reordenar_latidos(s3) -> None:
    """15 días son ~4.300 bloques: la fila del estudio no puede llevar 4.300 entradas."""
    size = 150_000
    study = _study(None, 0)
    state = None
    expected = []
    blocks = beat_label_store.TIER_FANOUT * 2 + 7
    for block in range(blocks):
        state = _append(study.id, state, block, size, count=3, before=block * size)
        expected.append(_records(block * size, 3))
    study.ml_state = {beat_label_store.STATE_KEY: state}
    study.ml_analyzed_samples = blocks * size

    assert [chunk["tier"] for chunk in state["chunks"]] == [1, 1] + [0] * 7
    stream = beat_label_store.load_beat_labels(study)
    assert stream is not None
    assert stream.records.tobytes() == np.concatenate(expected).tobytes()
    # El fundido cubre exactamente los bloques que reemplaza.
    first = state["chunks"][0]
    assert first["startSampleIndex"] == 0
    assert first["sampleCount"] == beat_label_store.TIER_FANOUT * size
    assert first["beatCount"] == 3 * beat_label_store.TIER_FANOUT


def test_al_cerrar_el_estudio_se_funde_todo_en_un_chunk(s3) -> None:
    size = 150_000
    study = _study(None, 0)
    state = None
    for block in range(30):
        state = _append(study.id, state, block, size, count=2, before=block * size)
    study.ml_state = {beat_label_store.STATE_KEY: state}
    study.ml_analyzed_samples = 30 * size
    before = beat_label_store.load_beat_labels(study)

    fused = beat_label_store.fuse_all(study)

    assert fused is not None and len(fused["chunks"]) == 1
    study.ml_state = {beat_label_store.STATE_KEY: fused}
    after = beat_label_store.load_beat_labels(study)
    assert before is not None and after is not None
    assert after.records.tobytes() == before.records.tobytes()
    assert beat_label_store.fuse_all(study) is None  # ya es uno solo


def test_si_el_motor_analizo_bloques_sin_clasificar_la_racha_vuelve_a_empezar(s3) -> None:
    """Con la clasificación apagada el cursor avanza sin etiquetas: esos bloques no
    se cuentan como "sin ectópicas", y la cobertura arranca donde vuelve a haber."""
    size = 150_000
    study_id = uuid.uuid4()
    state = _append(study_id, None, 0, size, count=4, before=0)

    resumed = _append(study_id, state, 3, size, count=4, before=3 * size)

    assert resumed["fromSample"] == 3 * size
    assert len(resumed["chunks"]) == 1


def test_un_formato_desconocido_no_se_lee_a_ciegas(s3) -> None:
    size = 150_000
    state = _append(uuid.uuid4(), None, 0, size, count=2, before=0)
    study = _study({beat_label_store.STATE_KEY: {**state, "format": 99}}, size)

    with pytest.raises(ValueError, match="formato"):
        beat_label_store.load_beat_labels(study)


def test_guardar_el_banco_conserva_la_lista_de_etiquetas() -> None:
    """`bank_to_state` arma el estado desde cero: sin esto, la fusión del cierre
    de morfologías borraría las etiquetas del estudio."""
    labels = {"format": 1, "classifier": "x", "fromSample": 0, "chunks": []}
    config = build_config(settings, SAMPLE_RATE)
    study = SimpleNamespace(id=uuid.uuid4(), ml_state={beat_label_store.STATE_KEY: labels})

    ml_persistence.store_bank(study, empty_bank(config), 1000, merged=True)  # type: ignore[arg-type]

    assert study.ml_state[beat_label_store.STATE_KEY] == labels
    updated = {**labels, "fromSample": 5}
    ml_persistence.store_bank(study, empty_bank(config), 2000, beat_labels=updated)  # type: ignore[arg-type]
    assert study.ml_state[beat_label_store.STATE_KEY]["fromSample"] == 5
    assert morphology.STATE_SCHEMA_VERSION == study.ml_state["schemaVersion"]


async def test_la_ingesta_deja_las_etiquetas_en_el_estado_del_estudio(
    client, s3, db, monkeypatch, make_patient, make_device, make_study
) -> None:
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=930.0, ectopic_every=12)

    await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    await db.refresh(study)

    state = study.ml_state[beat_label_store.STATE_KEY]
    assert state["classifier"] == bl.CLASSIFIER_VERSION and state["fromSample"] == 0
    stream = await _load(study)
    assert stream is not None
    assert stream.until_sample == study.ml_analyzed_samples
    counts = np.bincount(stream.records["label"], minlength=256)
    # Los V son los ectópicos del generador que alcanzó a analizar el cursor.
    covered = signal.ectopic_peaks[signal.ectopic_peaks < stream.until_sample - 125]
    assert counts[bl.CODE_V] == pytest.approx(covered.size, abs=1)
    assert counts[bl.CODE_N] > 600
    # Y el estudio, ya cerrado, las funde en un solo objeto.
    await finalizar(db, monkeypatch, study.id)
    await db.refresh(study)
    assert len(study.ml_state[beat_label_store.STATE_KEY]["chunks"]) == 1
    closed = await _load(study)
    assert closed is not None and closed.records.size >= stream.records.size


async def _load(study) -> bl.BeatLabelStream | None:
    import asyncio

    return await asyncio.to_thread(processing.load_beat_labels, study)


def _vt_signal(*, with_firmware: bool) -> np.ndarray:
    times = [1.0 + k for k in range(60)]
    run = [60.0 + 0.45 * k for k in range(1, 7)]
    after = [run[-1] + 1.0 + k for k in range(55)]
    wide = frozenset(range(len(times), len(times) + len(run)))
    ecg = synth_conduction([*times, *run, *after], duration_s=122.0, wide=wide)
    return ecg if with_firmware else replace(ecg, flags=np.zeros_like(ecg.flags))


async def test_la_tpv_alerta_siempre_pero_solo_avisa_al_paciente_si_el_firmware_confirma(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    results = {}
    for with_firmware in (True, False):
        patient, device, api_key, study = await _world(make_patient, make_device, make_study)
        ecg = _vt_signal(with_firmware=with_firmware)
        await _ingest(client, db, device, api_key, _frames(ecg.signal_mv, ecg.flags))
        patient_id, study_id = patient.id, study.id
        before = len(sent_pushes)
        await finalizar(db, monkeypatch, study_id)
        event = next(e for e in await _events(db, study_id) if e.event_metadata["kind"] == "vt_run")
        alert = (await db.scalars(select(Alert).where(Alert.event_id == event.id))).one()
        pushes = [
            item[1].data["kind"]
            for item in sent_pushes[before:]
            if item[1].data.get("type") == "report_request" and item[0] == patient_id
        ]
        # Valores planos: la sesión del test expira los objetos al siguiente estudio.
        results[with_firmware] = (
            event.event_type,
            event.severity,
            alert.kind,
            alert.message,
            pushes,
        )

    event_type, severity, kind, message, pushes = results[True]
    assert event_type is ECGEventType.TACHYCARDIA and severity is ECGEventSeverity.HIGH
    assert kind == "vt_run" and "posible" in message
    assert pushes == ["vt_run"]
    _, _, kind_without, _, pushes_without = results[False]
    assert kind_without == "vt_run"  # la alerta llega a la bandeja del médico
    assert pushes_without == []  # y el paciente no se entera sin R confirmados


async def test_el_motor_no_toca_un_hallazgo_que_el_medico_ya_corrigio(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """Un episodio confirmado tiene su tramo congelado: una corrida nueva que lo
    tocaría no lo empalma, no le sube la severidad ni le cambia la duración."""
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=60.0)
    body = await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    config = build_config(settings, SAMPLE_RATE)
    scope = ml_persistence.BlockScope(
        batch_id=batch.id,
        boot_id=batch.boot_id,
        run_start=0,
        read_start=0,
        block_start=0,
        block_end=60 * SAMPLE_RATE,
    )

    def result(start_s: float, length_s: float, severity: ECGEventSeverity) -> PipelineResult:
        start = int(start_s * SAMPLE_RATE)
        finding = Finding(
            kind="vt_run",
            event_type=ECGEventType.TACHYCARDIA,
            severity=severity,
            start_sample=start,
            length_samples=int(length_s * SAMPLE_RATE),
            dedupe_key=f"vt_run:{start}",
            beat_count=10,
            alert_message="Se detectó un posible episodio de taquicardia ventricular.",
            beat_samples=(start,),
            metadata={"durationSeconds": length_s, "sustained": 0},
        )
        return PipelineResult(
            quality_intervals=(),
            findings=(finding,),
            bank=empty_bank(config),
            metrics={},
            model_version=PIPELINE_VERSION,
        )

    await ml_persistence.persist_analysis(
        db, study, result(30, 4, ECGEventSeverity.HIGH), SAMPLE_RATE, scope
    )
    original = next(e for e in await _events(db, study.id) if e.event_metadata["kind"] == "vt_run")
    original.validation_status = ECGEventValidation.CONFIRMED
    await db.flush()
    span = (original.timestamp_in_recording, original.duration_seconds)

    # Una corrida que continúa la anterior y llega a sostenida: sin la corrección se
    # empalmaría (30 + 40 s) y la subiría a CRITICAL.
    await ml_persistence.persist_analysis(
        db, study, result(33, 40, ECGEventSeverity.HIGH), SAMPLE_RATE, scope
    )

    events = [e for e in await _events(db, study.id) if e.event_metadata["kind"] == "vt_run"]
    frozen = next(e for e in events if e.id == original.id)
    assert (frozen.timestamp_in_recording, frozen.duration_seconds) == span
    assert frozen.severity is ECGEventSeverity.HIGH
    assert len(events) == 2  # la nueva es otro evento, no una extensión del corregido


async def test_una_corrida_empalmada_que_cruza_30_s_sube_a_critica_y_avisa(
    client, s3, db, sent_pushes, make_patient, make_device, make_study
) -> None:
    """Dos bloques ven la misma TPV: ninguno la ve sostenida, el empalme sí."""
    patient, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=60.0)
    body = await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    config = build_config(settings, SAMPLE_RATE)
    scope = ml_persistence.BlockScope(
        batch_id=batch.id,
        boot_id=batch.boot_id,
        run_start=0,
        read_start=0,
        block_start=0,
        block_end=60 * SAMPLE_RATE,
    )

    def result(start_s: float, length_s: float) -> PipelineResult:
        start = int(start_s * SAMPLE_RATE)
        finding = Finding(
            kind="vt_run",
            event_type=ECGEventType.TACHYCARDIA,
            severity=ECGEventSeverity.HIGH,
            start_sample=start,
            length_samples=int(length_s * SAMPLE_RATE),
            dedupe_key=f"vt_run:{start}",
            beat_count=20,
            alert_message="Se detectó un posible episodio de taquicardia ventricular.",
            push=False,
            beat_samples=(start,),
            metadata={"durationSeconds": length_s, "sustained": 0},
        )
        return PipelineResult(
            quality_intervals=(),
            findings=(finding,),
            bank=empty_bank(config),
            metrics={},
            model_version=PIPELINE_VERSION,
        )

    _, first = await ml_persistence.persist_analysis(db, study, result(10, 20), SAMPLE_RATE, scope)
    _, second = await ml_persistence.persist_analysis(db, study, result(25, 20), SAMPLE_RATE, scope)

    event = next(e for e in await _events(db, study.id) if e.event_metadata["kind"] == "vt_run")
    assert event.severity is ECGEventSeverity.CRITICAL  # 35 s: sostenida
    assert event.event_metadata["sustained"] == 1
    assert first is None  # no sostenida y sin firmware: alerta sin push
    assert second is not None  # al cruzar los 30 s, avisa
    alert = (await db.scalars(select(Alert).where(Alert.event_id == event.id))).one()
    assert alert.severity.value == "CRITICAL"


async def test_las_ectopicas_comparten_el_tope_de_anomalias_por_estudio(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    _, device, api_key, study = await _world(make_patient, make_device, make_study)
    signal = synth_ecg(duration_s=60.0)
    body = await _ingest(client, db, device, api_key, _frames(signal.signal_mv, signal.flags))
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    config = build_config(settings, SAMPLE_RATE)
    scope = ml_persistence.BlockScope(
        batch_id=batch.id,
        boot_id=batch.boot_id,
        run_start=0,
        read_start=0,
        block_start=0,
        block_end=60 * SAMPLE_RATE,
    )
    findings = tuple(
        Finding(
            kind=kind,
            event_type=event_type,
            severity=ECGEventSeverity.LOW,
            start_sample=(10 + 5 * index) * SAMPLE_RATE,
            length_samples=SAMPLE_RATE,
            dedupe_key=f"{kind}:{(10 + 5 * index) * SAMPLE_RATE}",
            score=0.5,
        )
        for index, (kind, event_type) in enumerate(
            [
                ("morphology_anomaly", ECGEventType.ANOMALY),
                ("ventricular_ectopy", ECGEventType.ECTOPY),
                ("supraventricular_ectopy", ECGEventType.ECTOPY),
                ("vt_run", ECGEventType.TACHYCARDIA),
            ]
        )
    )
    await ml_persistence.persist_analysis(
        db,
        study,
        PipelineResult(
            quality_intervals=(),
            findings=findings,
            bank=empty_bank(config),
            metrics={},
            model_version=PIPELINE_VERSION,
        ),
        SAMPLE_RATE,
        scope,
    )

    # Cuentan la anomalía y las dos ectópicas; la corrida es de ritmo.
    assert await ml_persistence.count_anomalies(db, study.id) == 3
    assert len([e for e in await _events(db, study.id) if e.event_type is ECGEventType.ECTOPY]) == 2
