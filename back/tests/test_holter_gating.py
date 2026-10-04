"""El motor de detección frena y limpia las métricas Holter del informe.

Las métricas (`GET /studies/{id}/holter-metrics`, congeladas en el snapshot del
informe) son de Pan-Tompkins, y el motor no les agrega números: solo decide qué
señal entra. Lo que fija este archivo:

- **G1.** Las ventanas que el motor marcó `bad` por ruido (pSQI, kSQI, basSQI)
  salen de la FC, la VFC y la cuenta de latidos como una exclusión más, **pero
  no de las pausas**: una asistolia es señal sin QRS y el gate la marca `bad`
  por pSQI igual que al ruido (lo fija un test de punta a punta acá abajo).
  `no_beats` y `marginal` no excluyen nada.
- **G3.** Un riel (`flatline`) no es ruido sino señal que falta: sale de todo
  **pausas incluidas**, igual que un `lead_off` sobre el mismo tramo. Es lo que
  alinea el informe con el motor, que no infiere una pausa a través de un riel
  sin `LEAD_OFF` y no le avisa al paciente (también de punta a punta).
- **La versión dice la verdad.** Solo es la vigente (`ALGORITHM_VERSION`) si
  el motor evaluó toda la señal analizada: con el motor apagado, o con filas
  que no la cubren desde el inicio, las métricas son las del algoritmo 1
  enteras —sin el ruido ni los rieles— y lo declaran.
- **frame_gap** corta el RR: el buffer empaquetado pega las dos tramas y el RR
  que cruza el empalme mide adquisición perdida, no el corazón.
- **G2.** Con el motor prendido, el informe final espera a que su cursor cubra
  toda la señal (`ML_ANALYSIS_PENDING`): las métricas dependen de él, y un
  preview anterior al cierre no hashearía igual que el final. Con el motor
  apagado no hay nada que esperar.

Mismo estudio que `test_studies_holter_metrics`: 6 min a 75 lpm con una pausa
de 2,6 s que arranca en el latido 200.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityInterval, SignalQualityLevel
from app.db.models.user import User
from app.ml.holter_metrics import ALGORITHM_VERSION
from tests.ecg_synth import SAMPLE_RATE
from tests.test_ml_blocks import _Chaleco, _ecg, _estudio, _eventos_del_motor, _latidos
from tests.test_ml_ingest import finalizar
from tests.test_studies_holter_metrics import RATE, _doctor_user, _study_with_signal


async def _lote(db, study) -> ECGBatch:  # type: ignore[no-untyped-def]
    lote = ECGBatch(
        device_id=study.device_id,
        study_id=study.id,
        received_at=datetime.now(UTC),
        batch_timestamp=0,
        duration_seconds=study.samples_count // RATE,
        sample_rate=RATE,
        num_channels=1,
        num_samples=study.samples_count,
        compression_type="rice",
        s3_key="",
        processing_status=ProcessingStatus.DONE,
        first_seq=0,
        last_seq=0,
    )
    db.add(lote)
    await db.flush()
    return lote


def _alrededor_de_la_pausa(times) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """Dos segundos antes del latido que abre la pausa y dos después del que la cierra."""
    return int(round((times[200] - 2.0) * RATE)), int(round((times[201] + 2.0) * RATE))


async def _calidad(  # type: ignore[no-untyped-def]
    db,
    study,
    level: SignalQualityLevel,
    reason: str,
    desde: int,
    hasta: int,
    *,
    cubre_todo: bool = True,
) -> None:
    """Un veredicto del motor en `[desde, hasta)`.

    Con `cubre_todo` el resto de la señal queda `good`, como la deja el motor
    después de evaluarla entera: sin eso las métricas no le creen (versión 1).
    """
    lote = await _lote(db, study)
    tramos = [(level, reason, desde, hasta)]
    if cubre_todo:
        tramos += [
            (SignalQualityLevel.GOOD, "ok", 0, desde),
            (SignalQualityLevel.GOOD, "ok", hasta, study.samples_count),
        ]
    for nivel, motivo, inicio, fin in tramos:
        db.add(
            SignalQualityInterval(
                study_id=study.id,
                batch_id=lote.id,
                start_sample_index=inicio,
                sample_count=fin - inicio,
                level=nivel,
                reason=motivo,
                window_count=1,
                metrics=None,
                model_version="test",
            )
        )
    await db.commit()


async def _metricas(db, as_user, doctor, study) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    response = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/holter-metrics"
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["status"] == "ok"
    return body


async def _preview(db, as_user, doctor, study) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    response = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/clinical-report/preview"
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _codigos(preview: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in preview["issues"] if issue["severity"] == "blocking"}


# --------------------------------------------------------------------------- #
# G1: el ruido del motor sale de las métricas
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reason", ["psqi", "ksqi", "bassqi"])
async def test_el_ruido_que_marca_el_motor_sale_de_la_fc_pero_no_de_las_pausas(
    reason, db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Una ventana `bad` por ruido sobre la pausa: sus latidos no cuentan para
    la FC ni la VFC, y el análisis declara el tramo excluido. La pausa queda:
    el gate no distingue ruido de una asistolia, y el médico la descarta
    mirando su tira."""
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    antes = await _metricas(db, as_user, doctor, study)
    desde, hasta = _alrededor_de_la_pausa(times)

    await _calidad(db, study, SignalQualityLevel.BAD, reason, desde, hasta)
    despues = await _metricas(db, as_user, doctor, study)

    assert antes["analysis"]["algorithmVersion"] == 1
    assert despues["analysis"]["algorithmVersion"] == ALGORITHM_VERSION == 3
    assert antes["analysis"]["excludedMs"] == 0
    assert despues["analysis"]["excludedMs"] == (hasta - desde) * 1000 // RATE
    assert despues["analysis"]["analyzedMs"] == (
        antes["analysis"]["analyzedMs"] - despues["analysis"]["excludedMs"]
    )
    # Los latidos de adentro no se cuentan: 2 s antes y 2 s después a 75 lpm.
    assert despues["heartRate"]["totalBeats"] < antes["heartRate"]["totalBeats"]
    assert despues["analysis"]["rrIntervals"] < antes["analysis"]["rrIntervals"]
    assert despues["pauses"] == antes["pauses"]
    assert despues["pauses"]["count"] == 1


@pytest.mark.parametrize(
    ("level", "reason"),
    [
        (SignalQualityLevel.BAD, "no_beats"),
        (SignalQualityLevel.MARGINAL, "bsqi"),
        # Las filas de antes de separar los índices (y de quitar la red).
        (SignalQualityLevel.BAD, "spectral"),
        (SignalQualityLevel.GOOD, "ok"),
    ],
)
async def test_un_veredicto_que_no_es_ruido_no_excluye_nada(
    level, reason, db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    desde, hasta = _alrededor_de_la_pausa(times)

    await _calidad(db, study, level, reason, desde, hasta)
    body = await _metricas(db, as_user, doctor, study)

    assert body["analysis"]["algorithmVersion"] == ALGORITHM_VERSION
    assert body["pauses"]["count"] == 1
    assert abs(body["pauses"]["longest"]["durationMs"] - 2600) <= 10
    assert body["analysis"]["excludedMs"] == 0


# --------------------------------------------------------------------------- #
# G3: un riel del motor sale como el hardware, pausas incluidas
# --------------------------------------------------------------------------- #


async def _lead_off(db, study, desde: int, hasta: int) -> None:  # type: ignore[no-untyped-def]
    """El evento de la Capa A que deja `derive_events` con `LEAD_OFF` en los flags."""
    lote = await _lote(db, study)
    db.add(
        ECGEvent(
            batch_id=lote.id,
            study_id=study.id,
            event_type=ECGEventType.NOISE,
            severity=ECGEventSeverity.MEDIUM,
            timestamp_in_recording=desde / RATE,
            event_metadata={
                "kind": "lead_off",
                "startSampleIndex": desde,
                "sampleCount": hasta - desde,
            },
        )
    )
    await db.commit()


async def test_un_riel_del_motor_sale_de_las_pausas_como_un_lead_off(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Un riel sin `LEAD_OFF` sobre la pausa (segmento viejo, ADC congelado,
    corto): el motor lo declara `flatline` y no infiere una pausa a través de
    él. El informe tampoco: el resultado es el mismo que con un `lead_off` de
    la Capa A sobre el mismo tramo. Antes listaba la pausa."""
    doctor, riel, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    antes = await _metricas(db, as_user, doctor, riel)
    desde, hasta = _alrededor_de_la_pausa(times)
    await _calidad(db, riel, SignalQualityLevel.BAD, "flatline", desde, hasta)
    con_riel = await _metricas(db, as_user, doctor, riel)

    doctor, desconectado, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    await _calidad(
        db,
        desconectado,
        SignalQualityLevel.GOOD,
        "ok",
        0,
        desconectado.samples_count,
        cubre_todo=False,
    )
    await _lead_off(db, desconectado, desde, hasta)
    con_lead_off = await _metricas(db, as_user, doctor, desconectado)

    assert antes["pauses"]["count"] == 1
    assert con_riel["pauses"]["count"] == 0
    assert con_riel["analysis"]["algorithmVersion"] == ALGORITHM_VERSION == 3
    assert con_riel["analysis"]["excludedMs"] == (hasta - desde) * 1000 // RATE
    assert con_riel["heartRate"]["totalBeats"] < antes["heartRate"]["totalBeats"]
    assert con_riel["analysis"] == con_lead_off["analysis"]
    assert con_riel["pauses"] == con_lead_off["pauses"]
    assert con_riel["heartRate"]["totalBeats"] == con_lead_off["heartRate"]["totalBeats"]


async def test_el_ruido_sigue_sin_tocar_las_pausas_aunque_haya_un_riel(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """El riel corta las pausas; el ruido no. pSQI sobre la pausa y un riel
    lejos de ella: los dos salen de la FC y la pausa queda, porque el gate
    marca así también una asistolia."""
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    desde, hasta = _alrededor_de_la_pausa(times)
    riel_desde, riel_hasta = int(round(times[20] * RATE)), int(round(times[40] * RATE))
    await _calidad(db, study, SignalQualityLevel.BAD, "psqi", desde, hasta, cubre_todo=False)
    await _calidad(
        db, study, SignalQualityLevel.BAD, "flatline", riel_desde, riel_hasta, cubre_todo=False
    )
    await _calidad(
        db, study, SignalQualityLevel.GOOD, "ok", 0, study.samples_count, cubre_todo=False
    )

    body = await _metricas(db, as_user, doctor, study)

    assert body["analysis"]["algorithmVersion"] == ALGORITHM_VERSION
    assert (
        body["analysis"]["excludedMs"]
        == ((hasta - desde) + (riel_hasta - riel_desde)) * 1000 // RATE
    )
    assert body["pauses"]["count"] == 1
    assert abs(body["pauses"]["longest"]["durationMs"] - 2600) <= 10


async def test_sin_el_veredicto_completo_del_motor_el_riel_no_se_aplica(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Como el ruido: con filas que no cubren la señal, las métricas son las
    del algoritmo 1 enteras, que no conoce rieles."""
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    antes = await _metricas(db, as_user, doctor, study)
    desde, hasta = _alrededor_de_la_pausa(times)

    await _calidad(db, study, SignalQualityLevel.BAD, "flatline", desde, hasta, cubre_todo=False)
    despues = await _metricas(db, as_user, doctor, study)

    assert despues == antes
    assert despues["analysis"]["algorithmVersion"] == 1
    assert despues["pauses"]["count"] == 1


async def test_si_el_motor_no_evaluo_toda_la_senal_las_metricas_son_las_de_la_version_1(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Filas que no cubren la señal desde el inicio: un estudio que estaba en
    curso cuando se desplegó el motor, o uno con el motor apagado un tramo. El
    ruido que sí marcó no se aplica, y la versión no promete que se aplicó."""
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    antes = await _metricas(db, as_user, doctor, study)
    desde, hasta = _alrededor_de_la_pausa(times)

    await _calidad(db, study, SignalQualityLevel.BAD, "psqi", desde, hasta, cubre_todo=False)
    despues = await _metricas(db, as_user, doctor, study)

    assert despues == antes
    assert despues["analysis"]["algorithmVersion"] == 1
    assert despues["analysis"]["excludedMs"] == 0


async def test_con_el_motor_apagado_y_sin_filas_la_version_es_la_1(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )

    body = await _metricas(db, as_user, doctor, study)

    assert body["analysis"]["algorithmVersion"] == 1


@pytest.mark.usefixtures("ml_engine")
async def test_con_el_motor_en_curso_lo_que_todavia_no_evaluo_no_baja_la_version(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """El motor espera bloques enteros y va detrás de los latidos: lo que no
    evaluó todavía entra sin filtrar, y el ruido de lo que sí evaluó sale."""
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    desde, hasta = _alrededor_de_la_pausa(times)
    await _calidad(db, study, SignalQualityLevel.BAD, "psqi", desde, hasta, cubre_todo=False)
    await _calidad(db, study, SignalQualityLevel.GOOD, "ok", 0, desde, cubre_todo=False)
    study.ml_analyzed_samples = hasta
    await db.commit()

    body = await _metricas(db, as_user, doctor, study)

    assert body["analysis"]["algorithmVersion"] == ALGORITHM_VERSION
    assert body["analysis"]["excludedMs"] == (hasta - desde) * 1000 // RATE


@pytest.mark.usefixtures("ml_engine")
@pytest.mark.parametrize("asistolia_s", [13.0, 26.0])
async def test_una_asistolia_que_el_motor_marca_como_ruido_sigue_en_el_informe(
    asistolia_s,
    client,
    s3,
    db,
    monkeypatch,
    as_user,
    make_doctor,
    make_patient,
    make_device,
    make_study,
) -> None:
    """De punta a punta, con el motor de verdad: 240 s a 60 lpm con una
    asistolia desde t = 100 s, por la ingesta real y cerrado.

    Fija la premisa que tumbó la primera versión de G1: el gate **no** marca la
    asistolia `no_beats` sino `bad`/pSQI —sus índices son cocientes de
    potencia, y el piso de ruido sin QRS es ruido de banda ancha—. Si G1 sacara
    las pausas con el ruido, el informe la perdería entera.
    """
    monkeypatch.setattr(settings, "ml_analysis_block_seconds", 60.0)
    monkeypatch.setattr(settings, "ml_analysis_context_seconds", 30.0)
    monkeypatch.setattr(settings, "ml_analysis_lookahead_seconds", 10.0)
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, api_key = await make_device(patient=patient)
    study = await make_study(patient, device)
    study_id, doctor_user_id = study.id, doctor.user_id
    chaleco = _Chaleco(client, db, device, api_key)
    latidos = [t for t in _latidos([(240.0, 60.0)]) if not 100.0 < t < 100.0 + asistolia_s]
    senal, flags = _ecg(latidos, 240.0)
    for inicio in range(0, 240, 15):
        tramo = slice(inicio * SAMPLE_RATE, (inicio + 15) * SAMPLE_RATE)
        await chaleco.enviar(senal[tramo], flags[tramo])
    await finalizar(db, monkeypatch, study_id)
    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.beats_analyzed_samples == study.samples_count
    filas = (
        await db.scalars(
            select(SignalQualityInterval).where(SignalQualityInterval.study_id == study_id)
        )
    ).all()
    ruido = [
        fila
        for fila in filas
        if fila.level is SignalQualityLevel.BAD and fila.reason in {"psqi", "ksqi", "bassqi"}
    ]
    assert ruido, "el gate dejó de marcar la asistolia como ruido: revisar este test"
    assert all(100 * SAMPLE_RATE <= fila.start_sample_index for fila in ruido)

    user = await db.get(User, doctor_user_id)
    body = (await as_user(user).get(f"/studies/{study_id}/holter-metrics")).json()
    preview = (await as_user(user).get(f"/studies/{study_id}/clinical-report/preview")).json()

    assert body["analysis"]["algorithmVersion"] == ALGORITHM_VERSION
    assert body["analysis"]["excludedMs"] > 0
    assert body["pauses"]["count"] == 1
    assert abs(body["pauses"]["longest"]["durationMs"] - (asistolia_s + 1) * 1000) <= 20
    assert preview["snapshot"]["metrics"]["pauses"] == body["pauses"]


@pytest.mark.usefixtures("ml_engine")
async def test_un_riel_sin_lead_off_no_es_una_pausa_ni_para_el_motor_ni_para_el_informe(
    client,
    s3,
    db,
    monkeypatch,
    sent_pushes,
    as_user,
    make_doctor,
    make_patient,
    make_device,
    make_study,
) -> None:
    """De punta a punta, con el motor de verdad: 240 s a 60 lpm con 30 s de
    riel en cero exacto desde t = 100 s y sin `LEAD_OFF` en los flags (un ADC
    congelado, un corto, un segmento viejo). El motor marca `flatline`, no
    escribe una pausa ni avisa; el informe tampoco lista una de 31 s —antes la
    listaba—. La asistolia de verdad sigue saliendo en los dos
    (`test_una_asistolia_que_el_motor_marca_como_ruido_sigue_en_el_informe`).
    """
    monkeypatch.setattr(settings, "ml_analysis_block_seconds", 60.0)
    monkeypatch.setattr(settings, "ml_analysis_context_seconds", 30.0)
    monkeypatch.setattr(settings, "ml_analysis_lookahead_seconds", 10.0)
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, api_key = await make_device(patient=patient)
    study = await make_study(patient, device)
    study_id, doctor_user_id = study.id, doctor.user_id
    chaleco = _Chaleco(client, db, device, api_key)
    senal, flags = _ecg(_latidos([(240.0, 60.0)]), 240.0, plano=(100.0, 130.0))
    for inicio in range(0, 240, 15):
        tramo = slice(inicio * SAMPLE_RATE, (inicio + 15) * SAMPLE_RATE)
        await chaleco.enviar(senal[tramo], flags[tramo])
    await finalizar(db, monkeypatch, study_id)
    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.beats_analyzed_samples == study.samples_count
    filas = (
        await db.scalars(
            select(SignalQualityInterval).where(SignalQualityInterval.study_id == study_id)
        )
    ).all()
    rieles = [
        fila for fila in filas if fila.level is SignalQualityLevel.BAD and fila.reason == "flatline"
    ]
    assert sum(fila.sample_count for fila in rieles) == 30 * SAMPLE_RATE
    assert await _eventos_del_motor(db, study_id, "pause") == []
    assert [p for p in sent_pushes if p[1].data.get("kind") == "pause"] == []

    user = await db.get(User, doctor_user_id)
    body = (await as_user(user).get(f"/studies/{study_id}/holter-metrics")).json()
    preview = (await as_user(user).get(f"/studies/{study_id}/clinical-report/preview")).json()

    assert body["analysis"]["algorithmVersion"] == ALGORITHM_VERSION
    assert body["analysis"]["excludedMs"] >= 30_000
    assert body["pauses"]["count"] == 0
    assert preview["snapshot"]["metrics"]["pauses"] == body["pauses"]


async def test_un_frame_gap_corta_el_rr_y_no_se_cuenta_como_pausa(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Adquisición perdida entre dos tramas de `seq` contiguo: el buffer
    empaquetado las pega, y el RR que cruza el empalme no es un RR."""
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    antes = await _metricas(db, as_user, doctor, study)
    empalme = int(round((times[200] + 1.3) * RATE))
    lote = await _lote(db, study)
    db.add(
        ECGEvent(
            batch_id=lote.id,
            study_id=study.id,
            event_type=ECGEventType.OTHER,
            severity=ECGEventSeverity.MEDIUM,
            timestamp_in_recording=empalme / RATE,
            event_metadata={
                "kind": "frame_gap",
                "startSampleIndex": empalme,
                "sampleCount": RATE,
            },
        )
    )
    await db.commit()

    despues = await _metricas(db, as_user, doctor, study)

    assert antes["pauses"]["count"] == 1
    assert despues["pauses"]["count"] == 0
    assert despues["analysis"]["rrIntervals"] == antes["analysis"]["rrIntervals"] - 1
    # Corta, no excluye: los latidos de los dos lados se siguen contando.
    assert despues["analysis"]["excludedMs"] == 0
    assert despues["heartRate"]["totalBeats"] == antes["heartRate"]["totalBeats"]


# --------------------------------------------------------------------------- #
# G2: el informe final espera al motor
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("ml_engine")
async def test_con_el_motor_prendido_el_informe_final_espera_su_cursor(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    study.ml_analyzed_samples = study.samples_count - 1
    await db.commit()

    pendiente = await _preview(db, as_user, doctor, study)

    assert pendiente["canFinalize"] is False
    assert "ML_ANALYSIS_PENDING" in _codigos(pendiente)
    # Los latidos sí están completos: frena solo el motor.
    assert "BEAT_ANALYSIS_PENDING" not in _codigos(pendiente)

    study.ml_analyzed_samples = study.samples_count
    await db.commit()

    completo = await _preview(db, as_user, doctor, study)

    assert "ML_ANALYSIS_PENDING" not in _codigos(completo)


async def test_con_el_motor_apagado_el_informe_final_no_lo_espera(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Apagar el motor es la salida si se traba en un bloque: el informe no
    puede quedar esperando un cursor que nadie va a mover."""
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    assert study.ml_analyzed_samples < study.samples_count

    preview = await _preview(db, as_user, doctor, study)

    assert "ML_ANALYSIS_PENDING" not in _codigos(preview)
