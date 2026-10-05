"""El motor por bloques detrás de un cursor (`processing.append_ml_analysis`).

Los lotes del chaleco son de ~15 s y casi nada de lo clínico cabe ahí: una
taquicardia tiene que sostenerse 30 s, una pausa o un R-R pueden caer sobre el
borde de un POST. El motor recorre cada corrida de la línea de tiempo en bloques
con contexto izquierdo, y estos tests cubren el contrato de esa recorrida y de
su persistencia: el cursor, las colas, los bordes de corrida, los empalmes de
episodios, la idempotencia, la atribución, los totales y los avisos.

Para que los tests sean cortos el bloque se achica a 60 s con 30 s de contexto
y 10 s de contexto derecho (`bloques_cortos`). Es la misma mecánica que con los
300/60/30 de producción. El contexto no baja de 30 s: es el mínimo de una
taquicardia (`ml_rhythm_min_seconds`), y con menos una que cruza el borde no la
ve entera ningún bloque (lo rechaza `Settings`).
La invarianza al tamaño de lote, con esos 300/60, está en
`test_ml_block_invariance`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.s3 import put_object
from app.db.models.alert import Alert
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.signal_quality import SignalQualityInterval, SignalQualityLevel
from app.db.models.study import Study
from app.db.models.user import UserRole
from app.ml.contracts import Finding, QualityWindow
from app.ml.decompression import FLAG_LEAD_OFF, FLAG_R_PEAK
from app.ml.pipeline import PIPELINE_VERSION, PipelineResult, build_config, empty_bank
from app.modules.ingest import ml_persistence, processing
from app.modules.ingest.processing import (
    _attribution_batch,
    _flags_known_range,
    _flags_range,
    append_ml_analysis,
    process_batch,
)
from tests.ecg_synth import FIRMWARE_R_PEAK_LAG_MS, SAMPLE_RATE, _beat, to_microvolts
from tests.frame_builder import Sample, encode_samples
from tests.ingest_helpers import STEP_MS, post_frames
from tests.test_ml_ingest import finalizar
from tests.test_studies_simulate_anomaly import _batch

pytestmark = pytest.mark.usefixtures("ml_engine")

BLOCK_S = 60
CONTEXT_S = 30
LOOKAHEAD_S = 10
BLOCK = BLOCK_S * SAMPLE_RATE
CONTEXT = CONTEXT_S * SAMPLE_RATE
LOOKAHEAD = LOOKAHEAD_S * SAMPLE_RATE


@pytest.fixture
def bloques_cortos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ml_analysis_block_seconds", float(BLOCK_S))
    monkeypatch.setattr(settings, "ml_analysis_context_seconds", float(CONTEXT_S))
    monkeypatch.setattr(settings, "ml_analysis_lookahead_seconds", float(LOOKAHEAD_S))


# --------------------------------------------------------------------------- #
# Señal
# --------------------------------------------------------------------------- #


def _latidos(tramos: list[tuple[float, float]], *, desde: float = 0.5) -> list[float]:
    """Instantes de R para tramos `(duración_s, lpm)` seguidos."""
    instantes: list[float] = []
    t, fin = desde, 0.0
    for duracion, lpm in tramos:
        fin += duracion
        while t < fin:
            instantes.append(t)
            t += 60.0 / lpm
    return instantes


def _ecg(
    latidos: list[float],
    duracion_s: float,
    *,
    plano: tuple[float, float] | None = None,
    ectopicos: frozenset[int] = frozenset(),
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    """ECG en mV con un R en cada instante, y `FLAG_R_PEAK` donde lo pone el MCU.

    `plano` deja un tramo en cero exacto, sin latidos ni ruido: una línea plana.
    `ectopicos` son los índices de latido con la forma del foco de
    `ecg_synth.synth_ecg` (QRS ancho, T invertida).
    """
    n = int(duracion_s * SAMPLE_RATE)
    señal = np.zeros(n, dtype=np.float64)
    flags = np.zeros(n, dtype=np.uint8)
    medio = SAMPLE_RATE // 2
    lag = int(FIRMWARE_R_PEAK_LAG_MS * SAMPLE_RATE / 1000.0)
    for indice, instante in enumerate(latidos):
        centro = int(instante * SAMPLE_RATE)
        if centro + medio >= n:
            break
        bajo, alto = max(centro - medio, 0), centro + medio
        ectopico = indice in ectopicos
        señal[bajo:alto] += _beat(
            (np.arange(bajo, alto) - centro) / SAMPLE_RATE,
            width=3.0 if ectopico else 1.0,
            invert_t=ectopico,
            amp=1.3 if ectopico else 1.0,
        )
        if centro + lag < n:
            flags[centro + lag] |= FLAG_R_PEAK
    señal += np.random.default_rng(seed).normal(0.0, 0.008, n)
    if plano is not None:
        inicio, fin = (int(value * SAMPLE_RATE) for value in plano)
        señal[inicio:fin] = 0.0
        flags[inicio:fin] = 0
    return señal.astype(np.float32), flags


def _sinusal(duracion_s: float, **kwargs: Any) -> tuple[np.ndarray, np.ndarray]:
    return _ecg(_latidos([(duracion_s, 60.0)]), duracion_s, **kwargs)


# --------------------------------------------------------------------------- #
# Ingesta
# --------------------------------------------------------------------------- #


@dataclass
class _Chaleco:
    """Un equipo que manda lotes con `seq` contiguo y `t0Ms` que sigue la señal.

    `corrida_nueva` vuelve el `t0Ms` a cero con el mismo `bootId`: la línea de
    tiempo lo lee como la vuelta de `millis()` y abre una corrida nueva, que es la
    forma más corta de tener dos corridas contiguas en el buffer empaquetado.
    """

    client: Any
    db: Any
    device: Any
    api_key: str
    seq: int = 0
    muestra: int = 0

    async def enviar(
        self,
        señal: np.ndarray,
        flags: np.ndarray,
        *,
        corrida_nueva: bool = False,
        salto_muestras: int = 0,
        procesar: bool = True,
    ) -> dict[str, Any]:
        """Un POST y su `process_batch`. Sin `procesar`, el lote queda archivado y en cola."""
        if corrida_nueva:
            self.muestra = 0
        self.muestra += salto_muestras
        muestras = [
            Sample(timestamp_ms=(self.muestra + index) * STEP_MS, raw_uV=[value], flags=int(flag))
            for index, (value, flag) in enumerate(zip(to_microvolts(señal), flags, strict=True))
        ]
        tramas = encode_samples(muestras, first_seq=self.seq, boot_id=0, simulated=True)
        self.seq += len(tramas)
        self.muestra += len(muestras)
        body = (await post_frames(self.client, self.device, self.api_key, tramas)).json()
        if procesar:
            await process_batch(self.db, body["batchId"])
        return body


async def _mundo(client, db, make_patient, make_device, make_study) -> tuple[_Chaleco, Study]:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    study = await make_study(patient, device)
    return _Chaleco(client, db, device, api_key), study


async def _estudio(db, study_id: uuid.UUID) -> Study:
    study = await db.get(Study, study_id)
    assert study is not None
    await db.refresh(study)
    return study


async def _eventos_del_motor(db, study_id: uuid.UUID, kind: str | None = None) -> list[ECGEvent]:
    filas = (
        await db.scalars(
            select(ECGEvent)
            .where(
                ECGEvent.study_id == study_id,
                ECGEvent.deleted_at.is_(None),
                ECGEvent.model_version.is_not(None),
            )
            .order_by(ECGEvent.timestamp_in_recording)
        )
    ).all()
    return [
        evento
        for evento in filas
        if kind is None or (evento.event_metadata or {}).get("kind") == kind
    ]


async def _calidad(db, study_id: uuid.UUID) -> list[SignalQualityInterval]:
    return list(
        (
            await db.scalars(
                select(SignalQualityInterval)
                .where(SignalQualityInterval.study_id == study_id)
                .order_by(SignalQualityInterval.start_sample_index)
            )
        ).all()
    )


def _espiar_motor(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Registra cada llamada al motor sin cambiar lo que hace."""
    llamadas: list[dict[str, Any]] = []
    real = processing.analyze_batch

    def _espia(signal, flags, **kwargs):
        llamadas.append({"n": int(signal.size), **kwargs})
        return real(signal, flags, **kwargs)

    monkeypatch.setattr(processing, "analyze_batch", _espia)
    return llamadas


# --------------------------------------------------------------------------- #
# Flags archivados
# --------------------------------------------------------------------------- #


async def test_cada_lote_archiva_sus_flags_y_los_marca_en_el_segmento(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    from app.core.s3 import get_object

    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(20.0)
    flags[1000:2000] |= FLAG_LEAD_OFF

    await chaleco.enviar(señal, flags)

    study = await _estudio(db, study.id)
    (segmento,) = study.ecg_segments
    # Una marca y no las claves: la de los flags sale del estudio y del `seq`.
    assert segmento["firstSeq"] == 0
    assert "flagsKey" not in segmento and "batchId" not in segmento
    archivados = np.frombuffer(
        get_object(processing.flags_key(study.id, segmento["firstSeq"])), dtype=np.uint8
    )
    assert np.array_equal(archivados, flags)


def test_flags_range_lee_lo_archivado_y_rellena_con_ceros_los_segmentos_viejos(s3) -> None:
    """Un segmento sin `firstSeq` es de antes de que el lote archivara sus flags:
    aporta ceros —ni veto de la Capa A ni picos del firmware—, no un error. Y
    la máscara de flags conocidos lo dice, para que el gate no le pida bSQI."""
    study_id = uuid.uuid4()
    put_object(processing.flags_key(study_id, 7), bytes([1, 2, 3, 4]))
    segmentos = [
        {"key": "a.f32", "startSampleIndex": 0, "sampleCount": 4, "firstSeq": 7},
        {"key": "b.f32", "startSampleIndex": 4, "sampleCount": 3},
        {"key": "c.f32", "startSampleIndex": 9, "sampleCount": 2},
    ]

    assert _flags_range(study_id, segmentos, 2, 6).tolist() == [3, 4, 0, 0]
    assert _flags_range(study_id, segmentos, 0, 4).tolist() == [1, 2, 3, 4]
    assert _flags_range(study_id, segmentos, 2, 6).dtype == np.uint8
    assert _flags_known_range(segmentos, 2, 6).tolist() == [True, True, False, False]
    # Las muestras 7-8 no están en ningún segmento: es un hueco del buffer, que
    # por construcción no puede existir.
    with pytest.raises(RuntimeError):
        _flags_range(study_id, segmentos, 5, 10)


# --------------------------------------------------------------------------- #
# El cursor
# --------------------------------------------------------------------------- #


async def test_el_cursor_avanza_de_a_bloques_enteros_y_espera_la_cola_con_la_corrida_abierta(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    llamadas = _espiar_motor(monkeypatch)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(135.0)

    await chaleco.enviar(señal[: 90 * SAMPLE_RATE], flags[: 90 * SAMPLE_RATE])
    assert (await _estudio(db, study.id)).ml_analyzed_samples == BLOCK
    await chaleco.enviar(señal[90 * SAMPLE_RATE :], flags[90 * SAMPLE_RATE :])
    assert (await _estudio(db, study.id)).ml_analyzed_samples == 2 * BLOCK

    # Bloques enteros, el segundo con su contexto izquierdo de la misma
    # corrida, y los dos con su contexto derecho.
    assert [
        (c["start_sample_index"], c["n"], c["context_samples"], c["lookahead_samples"])
        for c in llamadas
    ] == [
        (0, BLOCK + LOOKAHEAD, 0, LOOKAHEAD),
        (BLOCK - CONTEXT, BLOCK + CONTEXT + LOOKAHEAD, CONTEXT, LOOKAHEAD),
    ]
    # La cola (15 s) espera: con la corrida abierta, el lote siguiente la puede
    # completar.
    assert all(fila.start_sample_index < 2 * BLOCK for fila in await _calidad(db, study.id))

    study_id = study.id
    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.samples_count == 135 * SAMPLE_RATE
    # La cola de la corrida cerrada no tiene contexto derecho: después no hay señal.
    assert llamadas[-1]["start_sample_index"] == 2 * BLOCK - CONTEXT
    assert llamadas[-1]["lookahead_samples"] == 0


async def test_la_cola_se_analiza_al_abrirse_otra_corrida_y_ningun_bloque_cruza_el_borde(
    client,
    s3,
    db,
    monkeypatch,
    as_user,
    make_user,
    bloques_cortos,
    make_patient,
    make_device,
    make_study,
) -> None:
    """Dos corridas son contiguas en el buffer empaquetado, pero entre ellas hay
    un hueco real de grabación: ni un R-R, ni un bloque, ni un intervalo de
    calidad pueden atravesarlo."""
    llamadas = _espiar_motor(monkeypatch)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    primera, primeros_flags = _sinusal(90.0)
    segunda, segundos_flags = _sinusal(70.0, seed=11)

    await chaleco.enviar(primera, primeros_flags)
    assert (await _estudio(db, study.id)).ml_analyzed_samples == BLOCK
    await chaleco.enviar(segunda, segundos_flags, corrida_nueva=True)
    # Abrir la segunda cerró la primera: su cola se analizó. La segunda tiene un
    # bloque entero (60 de 70 s) y su cola espera.
    borde = 90 * SAMPLE_RATE
    assert (await _estudio(db, study.id)).ml_analyzed_samples == borde + BLOCK

    study_id = study.id
    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.samples_count == 160 * SAMPLE_RATE
    for llamada in llamadas:
        inicio = llamada["start_sample_index"]
        fin = inicio + llamada["n"]
        assert fin <= borde or inicio >= borde, "un bloque leyó a través del borde de corrida"
    for fila in await _calidad(db, study_id):
        fin = fila.start_sample_index + fila.sample_count
        assert fin <= borde or fila.start_sample_index >= borde

    # Y la lectura tampoco funde a través del borde: las dos corridas son
    # buenas y contiguas en muestras, pero son dos intervalos con su hora real.
    as_user(await make_user(UserRole.ADMIN))
    body = (await client.get(f"/studies/{study_id}/findings")).json()
    intervalos = body["quality"]["intervals"]
    assert [item["level"] for item in intervalos] == ["good", "good"]
    assert intervalos[0]["endOffsetMs"] == intervalos[1]["startOffsetMs"] == 90_000
    manifest = (await client.get(f"/studies/{study_id}/ecg/manifest")).json()
    tramos = manifest["timeline"]
    assert intervalos[0]["endEpochMs"] == tramos[0]["endEpochMs"]
    assert intervalos[1]["startEpochMs"] == tramos[1]["startEpochMs"]


async def test_los_empalmes_de_la_corrida_llegan_al_motor(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """Un salto chico de `t0Ms` entre dos lotes no abre corrida: es un `frame_gap`
    adentro de la misma. El motor lo recibe como empalme, ubicado en el bloque."""
    llamadas = _espiar_motor(monkeypatch)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(100.0)

    await chaleco.enviar(señal[: 50 * SAMPLE_RATE], flags[: 50 * SAMPLE_RATE])
    # Medio segundo sin adquirir, por debajo de la tolerancia que abre corrida.
    await chaleco.enviar(
        señal[50 * SAMPLE_RATE :], flags[50 * SAMPLE_RATE :], salto_muestras=SAMPLE_RATE // 2
    )

    (hueco,) = [
        evento
        for evento in (
            await db.scalars(select(ECGEvent).where(ECGEvent.study_id == study.id))
        ).all()
        if evento.event_metadata["kind"] == "frame_gap"
    ]
    assert hueco.event_metadata["startSampleIndex"] == 50 * SAMPLE_RATE
    (bloque,) = llamadas
    # Relativo al inicio de lo que leyó el bloque, que acá es el de la corrida.
    assert bloque["gap_samples"] == ((50 * SAMPLE_RATE, hueco.event_metadata["sampleCount"]),)
    assert hueco.event_metadata["sampleCount"] == pytest.approx(SAMPLE_RATE // 2, abs=2)


# --------------------------------------------------------------------------- #
# Empalmes de episodios
# --------------------------------------------------------------------------- #


async def _analizar_entero(chaleco: _Chaleco, study: Study, señal, flags, monkeypatch) -> uuid.UUID:
    await chaleco.enviar(señal, flags)
    study_id = study.id
    await finalizar(db=chaleco.db, monkeypatch=monkeypatch, study_id=study_id)
    return study_id


async def test_una_taquicardia_mas_larga_que_un_bloque_es_un_solo_evento(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """Cien segundos de taquicardia cruzan dos bordes de bloque. El bloque que la
    ve seguir arranca su hallazgo en el contexto, y la persistencia lo empalma con
    el evento que escribió el bloque anterior: termina igual que si el registro se
    hubiera analizado de una sola vez."""
    latidos = _latidos([(60.0, 60.0), (100.0, 130.0), (40.0, 60.0)])
    señal, flags = _ecg(latidos, 200.0)

    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    por_bloques = await _analizar_entero(chaleco, study, señal, flags, monkeypatch)

    monkeypatch.setattr(settings, "ml_analysis_block_seconds", 600.0)
    otro, otro_estudio = await _mundo(client, db, make_patient, make_device, make_study)
    de_una_vez = await _analizar_entero(otro, otro_estudio, señal, flags, monkeypatch)

    (empalmado,) = await _eventos_del_motor(db, por_bloques, "tachycardia")
    (referencia,) = await _eventos_del_motor(db, de_una_vez, "tachycardia")
    meta, ref = empalmado.event_metadata, referencia.event_metadata
    assert meta["startSampleIndex"] == pytest.approx(ref["startSampleIndex"], abs=SAMPLE_RATE)
    fin = meta["startSampleIndex"] + meta["sampleCount"]
    assert fin == pytest.approx(ref["startSampleIndex"] + ref["sampleCount"], abs=SAMPLE_RATE)
    assert meta["durationSeconds"] == pytest.approx(100.0, abs=2.0)
    assert empalmado.duration_seconds == pytest.approx(meta["sampleCount"] / SAMPLE_RATE)
    assert empalmado.timestamp_in_recording == meta["startSampleIndex"] / SAMPLE_RATE
    assert meta["beatCount"] == pytest.approx(ref["beatCount"], rel=0.1)
    assert meta["peakBpm"] == pytest.approx(ref["peakBpm"], rel=0.05)


async def test_una_pausa_sobre_el_borde_de_un_bloque_se_detecta_y_avisa_una_vez(
    client, s3, db, monkeypatch, sent_pushes, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """El bloque que termina en el borde no ve el R siguiente; el que arranca ahí
    lo ve, con el anterior en su contexto. La pausa empieza en el contexto y se
    escribe una sola vez, con un solo aviso."""
    latidos = [t for t in _latidos([(120.0, 60.0)]) if not 58.6 < t < 61.6]
    latidos.append(61.4)
    latidos.sort()
    señal, flags = _ecg(latidos, 120.0)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)

    study_id = await _analizar_entero(chaleco, study, señal, flags, monkeypatch)

    (pausa,) = await _eventos_del_motor(db, study_id, "pause")
    assert pausa.event_metadata["startSampleIndex"] < BLOCK, "empieza en el contexto"
    assert pausa.event_metadata["pauseSeconds"] == pytest.approx(2.9, abs=0.05)
    alertas = (await db.scalars(select(Alert).where(Alert.event_id == pausa.id))).all()
    assert len(alertas) == 1
    assert len([item for item in sent_pushes if item[1].data.get("kind") == "pause"]) == 1


async def test_una_linea_plana_se_empalma_por_adyacencia_pero_no_entre_corridas(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """Las bandas de calidad nunca empiezan en el contexto, pero sí justo donde
    terminó la del bloque anterior: es la misma banda. Entre dos corridas, en
    cambio, lo contiguo en muestras no es contiguo en el tiempo."""
    # Una corrida: plano de 50 a 80 s, cruzando el borde de 60 s.
    señal, flags = _sinusal(120.0, plano=(50.0, 80.0))
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    una_corrida = await _analizar_entero(chaleco, study, señal, flags, monkeypatch)

    (plano,) = await _eventos_del_motor(db, una_corrida, "flatline")
    assert plano.event_metadata["startSampleIndex"] == 50 * SAMPLE_RATE
    assert plano.event_metadata["sampleCount"] == 30 * SAMPLE_RATE
    assert plano.event_metadata["durationSeconds"] == pytest.approx(30.0)

    # Dos corridas: el plano del final de la primera y el del principio de la
    # segunda quedan pegados en el buffer, y siguen siendo dos eventos.
    primera, primeros = _sinusal(60.0, plano=(50.0, 60.0))
    segunda, segundos = _sinusal(60.0, plano=(0.0, 10.0), seed=3)
    otro, otro_estudio = await _mundo(client, db, make_patient, make_device, make_study)
    await otro.enviar(primera, primeros)
    await otro.enviar(segunda, segundos, corrida_nueva=True)
    dos_corridas = otro_estudio.id
    await finalizar(db, monkeypatch, dos_corridas)

    planos = await _eventos_del_motor(db, dos_corridas, "flatline")
    assert [
        (e.event_metadata["startSampleIndex"], e.event_metadata["sampleCount"]) for e in planos
    ] == [
        (50 * SAMPLE_RATE, 10 * SAMPLE_RATE),
        (60 * SAMPLE_RATE, 10 * SAMPLE_RATE),
    ]


async def test_una_banda_que_cierra_su_corrida_no_dura_el_hueco_de_grabacion(
    client,
    s3,
    db,
    monkeypatch,
    as_user,
    make_user,
    bloques_cortos,
    make_patient,
    make_device,
    make_study,
) -> None:
    """La línea plana termina justo donde termina su corrida, y la siguiente
    arranca una hora después. Resolviendo el final exclusivo por la muestra, la
    hora de pared caía en la corrida siguiente: la banda de 10 s duraba casi una
    hora en `/findings`, igual que su intervalo de calidad antes de este cambio.
    """
    primera, primeros = _sinusal(60.0, plano=(50.0, 60.0))
    segunda, segundos = _sinusal(30.0, seed=3)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    study_id = study.id
    await chaleco.enviar(primera, primeros)
    await chaleco.enviar(segunda, segundos, salto_muestras=3600 * SAMPLE_RATE)
    await finalizar(db, monkeypatch, study_id)

    as_user(await make_user(UserRole.ADMIN))
    body = (await client.get(f"/studies/{study_id}/findings")).json()
    planos = [
        item for grupo in body["groups"] for item in grupo["items"] if item["kind"] == "flatline"
    ]
    assert len(planos) == 1
    assert planos[0]["endEpochMs"] - planos[0]["startEpochMs"] == pytest.approx(10_000, abs=2)
    (grupo,) = [grupo for grupo in body["groups"] if grupo["kind"] == "flatline"]
    assert grupo["lastEpochMs"] - grupo["firstEpochMs"] <= 10_002
    malos = [i for i in body["quality"]["intervals"] if i["level"] != "good"]
    assert [i["endEpochMs"] - i["startEpochMs"] for i in malos] == [pytest.approx(10_000, abs=2)]


# --------------------------------------------------------------------------- #
# Persistencia: idempotencia, atribución y avisos
# --------------------------------------------------------------------------- #


def _scope(batch_id: uuid.UUID, *, block_start: int = 0, run_start: int = 0) -> Any:
    return ml_persistence.BlockScope(
        batch_id=batch_id,
        boot_id=0,
        run_start=run_start,
        read_start=max(run_start, block_start - CONTEXT_S * SAMPLE_RATE),
        block_start=block_start,
        block_end=block_start + BLOCK,
    )


def _resultado(*findings: Finding, calidad: tuple = ()) -> PipelineResult:
    return PipelineResult(
        quality_intervals=calidad,
        findings=findings,
        bank=empty_bank(build_config(settings, SAMPLE_RATE)),
        metrics={},
        model_version=PIPELINE_VERSION,
    )


def _taquicardia(inicio: int, largo: int, *, severa: bool, pico: float) -> Finding:
    return Finding(
        kind="tachycardia",
        event_type=ECGEventType.TACHYCARDIA,
        severity=ECGEventSeverity.HIGH if severa else ECGEventSeverity.MEDIUM,
        start_sample=inicio,
        length_samples=largo,
        dedupe_key=f"tachycardia:{inicio}",
        # Un latido por segundo: cuentas redondas para el empalme.
        beat_count=largo // SAMPLE_RATE,
        alert_message="Frecuencia cardíaca muy alta sostenida." if severa else None,
        metadata={"peakBpm": pico, "durationSeconds": round(largo / SAMPLE_RATE, 2)},
    )


async def _mundo_con_lotes(db, make_patient, make_device, make_study, cuantos: int = 2):
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    lotes = []
    for indice in range(cuantos):
        lote = await _batch(db, device, study, BLOCK)
        lote.first_seq = indice * 100
        lotes.append(lote)
    await db.flush()
    return study, lotes


async def test_los_intervalos_de_calidad_son_idempotentes_por_estudio_y_muestra(
    s3, db, make_patient, make_device, make_study
) -> None:
    """La clave es `(study_id, start_sample_index)` y no el lote: el mismo bloque
    escrito dos veces —aunque lo atribuya otro lote— no duplica nada."""
    study, (primero, segundo) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    ventana = QualityWindow(
        start_sample=0,
        length_samples=BLOCK,
        level=SignalQualityLevel.GOOD,
        reason="ok",
        psqi=0.8,
        ksqi=12.0,
        bassqi=0.99,
        bsqi=1.0,
    )
    resultado = _resultado(calidad=((ventana, 6),))

    await ml_persistence.persist_analysis(db, study, resultado, SAMPLE_RATE, _scope(primero.id))
    await ml_persistence.persist_analysis(db, study, resultado, SAMPLE_RATE, _scope(segundo.id))

    (fila,) = await _calidad(db, study.id)
    assert fila.batch_id == primero.id
    assert fila.window_count == 6


async def test_solo_avisan_los_eventos_nuevos_y_como_mucho_una_vez_por_evento(
    s3, db, make_patient, make_device, make_study
) -> None:
    """Un empalme no vuelve a avisar. Pero una taquicardia que empezó moderada y
    se vuelve severa en el bloque siguiente es, de una sola vez, un evento severo
    con su aviso: el empalme avisa si el evento todavía no tenía ninguno."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)

    async def persistir(*findings: Finding) -> Any:
        _, pushable = await ml_persistence.persist_analysis(
            db, study, _resultado(*findings), SAMPLE_RATE, _scope(lote.id)
        )
        return pushable

    assert await persistir(_taquicardia(1_000, 20_000, severa=False, pico=130.0)) is None
    escalada = await persistir(_taquicardia(15_000, 25_000, severa=True, pico=165.0))
    assert escalada is not None
    assert await persistir(_taquicardia(30_000, 20_000, severa=True, pico=170.0)) is None

    (evento,) = await _eventos_del_motor(db, study.id, "tachycardia")
    assert evento.event_metadata["startSampleIndex"] == 1_000
    assert evento.event_metadata["sampleCount"] == 49_000
    assert evento.event_metadata["peakBpm"] == 170.0
    assert evento.severity is ECGEventSeverity.HIGH
    alertas = (await db.scalars(select(Alert).where(Alert.event_id == evento.id))).all()
    assert [alerta.id for alerta in alertas] == [escalada.alert_id]


async def test_un_hallazgo_que_une_dos_eventos_los_funde_y_conserva_sus_avisos(
    s3, db, make_patient, make_device, make_study
) -> None:
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    for inicio in (300_000, 340_000):
        await ml_persistence.persist_analysis(
            db,
            study,
            _resultado(_taquicardia(inicio, 20_000, severa=True, pico=160.0)),
            SAMPLE_RATE,
            _scope(lote.id),
        )
    puente = _taquicardia(315_000, 30_000, severa=True, pico=158.0)
    await ml_persistence.persist_analysis(
        db, study, _resultado(puente), SAMPLE_RATE, _scope(lote.id)
    )

    (sobreviviente,) = await _eventos_del_motor(db, study.id, "tachycardia")
    assert sobreviviente.event_metadata["startSampleIndex"] == 300_000
    assert sobreviviente.event_metadata["sampleCount"] == 60_000
    # 40 + 40 latidos de los dos eventos (40 s cada uno), más la parte del
    # puente que no cubrían: 40 de sus 60 s, o sea 40 de sus 60 latidos.
    assert sobreviviente.event_metadata["beatCount"] == 120
    alertas = (await db.scalars(select(Alert).where(Alert.patient_id == study.patient_id))).all()
    assert len(alertas) == 2
    assert {alerta.event_id for alerta in alertas} == {sobreviviente.id}


def _pausa(inicio: int, segundos: float, severidad: ECGEventSeverity) -> Finding:
    return Finding(
        kind="pause",
        event_type=ECGEventType.PAUSE,
        severity=severidad,
        start_sample=inicio,
        length_samples=int(segundos * SAMPLE_RATE),
        dedupe_key=f"pause:{inicio}",
        beat_count=2,
        alert_message="Se detectó una pausa en el ritmo.",
        metadata={"pauseSeconds": segundos},
    )


def _hallazgo(kind: str, inicio: int, largo: int, *, foco: int | None = None) -> Finding:
    return Finding(
        kind=kind,
        event_type=ECGEventType.ANOMALY if foco is not None else ECGEventType.NOISE,
        severity=ECGEventSeverity.LOW,
        start_sample=inicio,
        length_samples=largo,
        dedupe_key=f"{kind}:{inicio}",
        cluster_id=foco,
    )


async def test_dos_taquicardias_a_menos_de_la_refractariedad_son_un_evento_y_un_aviso(
    s3, db, make_patient, make_device, make_study
) -> None:
    """De una sola vez, `apply_refractory` funde dos taquicardias separadas por
    6 s. Por bloques, la primera puede no volver a verse en el contexto (lo que
    entra de ella no llega a los 30 s), y la segunda llega sola, sin tocarla: se
    empalma igual, a través del hueco, con la misma ventana. Una banda de ruido
    no pasa por la refractariedad y a 6 s sigue siendo otra."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    hueco = 6 * SAMPLE_RATE
    primera = _taquicardia(100_000, 20_000, severa=True, pico=160.0)
    segunda = _taquicardia(120_000 + hueco, 20_000, severa=True, pico=165.0)
    pushes = []
    for finding in (primera, segunda):
        _, pushable = await ml_persistence.persist_analysis(
            db, study, _resultado(finding), SAMPLE_RATE, _scope(lote.id, block_start=120_000)
        )
        pushes.append(pushable)

    (evento,) = await _eventos_del_motor(db, study.id, "tachycardia")
    assert evento.event_metadata["startSampleIndex"] == 100_000
    assert evento.event_metadata["sampleCount"] == 40_000 + hueco
    assert evento.event_metadata["peakBpm"] == 165.0
    alertas = (await db.scalars(select(Alert).where(Alert.event_id == evento.id))).all()
    assert len(alertas) == 1
    assert pushes[0] is not None and pushes[1] is None

    for inicio in (100_000, 120_000 + hueco):
        await ml_persistence.persist_analysis(
            db,
            study,
            _resultado(_hallazgo("noise_burst", inicio, 20_000)),
            SAMPLE_RATE,
            _scope(lote.id, block_start=120_000),
        )
    assert len(await _eventos_del_motor(db, study.id, "noise_burst")) == 2


async def test_una_pausa_alta_que_se_empalma_con_una_critica_sube_su_aviso_a_critico(
    s3, db, make_patient, make_device, make_study
) -> None:
    """El bloque N avisa una pausa de 2,7 s (alta). El N+1 ve en su contexto esa
    pausa y otra de 3,5 s a 5 s: la refractariedad las funde en una crítica que
    se empalma con el evento. De una sola vez es una pausa crítica con un aviso
    crítico; por bloques, el evento sube a crítico y su aviso también —vuelve a
    no visto y se notifica de nuevo— para que la bandeja del médico no la ordene
    ni la filtre como alta."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    alta = _pausa(29_000, 2.7, ECGEventSeverity.HIGH)
    _, primero = await ml_persistence.persist_analysis(
        db, study, _resultado(alta), SAMPLE_RATE, _scope(lote.id)
    )
    assert primero is not None
    (alerta,) = (await db.scalars(select(Alert).where(Alert.patient_id == study.patient_id))).all()
    alerta.seen_at = alerta.created_at
    await db.flush()

    fundida = replace(
        _pausa(29_000, 3.5, ECGEventSeverity.CRITICAL),
        length_samples=int(11.2 * SAMPLE_RATE),
        metadata={"pauseSeconds": 3.5},
    )
    _, segundo = await ml_persistence.persist_analysis(
        db, study, _resultado(fundida), SAMPLE_RATE, _scope(lote.id, block_start=BLOCK)
    )

    (evento,) = await _eventos_del_motor(db, study.id, "pause")
    assert evento.severity is ECGEventSeverity.CRITICAL
    assert evento.event_metadata["pauseSeconds"] == 3.5
    (alerta,) = (await db.scalars(select(Alert).where(Alert.event_id == evento.id))).all()
    assert alerta.severity.name == "CRITICAL"
    assert alerta.seen_at is None
    assert segundo is not None and segundo.alert_id == alerta.id
    assert segundo.rank > primero.rank


async def test_los_episodios_de_morfologia_se_empalman_solo_con_su_mismo_foco(
    s3, db, make_patient, make_device, make_study
) -> None:
    """Un bigeminismo que cruza el borde llega del bloque siguiente como un
    episodio que arranca en el contexto y se solapa con el que ya está: es el
    mismo si es del mismo foco. Uno de otro foco en el mismo tramo es otro."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)

    async def persistir(*findings: Finding) -> None:
        await ml_persistence.persist_analysis(
            db, study, _resultado(*findings), SAMPLE_RATE, _scope(lote.id, block_start=BLOCK)
        )

    await persistir(_hallazgo("morphology_anomaly", 20_000, 10_000, foco=1))
    await persistir(
        _hallazgo("morphology_anomaly", 25_000, 15_000, foco=1),
        _hallazgo("morphology_anomaly", 26_000, 2_000, foco=2),
    )

    episodios = [
        (meta["clusterId"], meta["startSampleIndex"], meta["sampleCount"])
        for meta in (e.event_metadata for e in await _eventos_del_motor(db, study.id))
        if meta["kind"] == "morphology_anomaly"
    ]
    assert sorted(episodios) == [(1, 20_000, 20_000), (2, 26_000, 2_000)]


def _episodio(foco: int, latidos_s: list[float]) -> Finding:
    """Un episodio de morfología con un R en cada instante, y su ventana de latido."""
    picos = tuple(int(t * SAMPLE_RATE) for t in latidos_s)
    inicio, fin = picos[0] - SAMPLE_RATE // 4, picos[-1] + SAMPLE_RATE // 4
    return replace(
        _hallazgo("morphology_anomaly", inicio, fin - inicio, foco=foco),
        beat_count=len(picos),
        beat_samples=picos,
    )


async def test_los_latidos_de_un_episodio_empalmado_son_la_union_de_los_de_cada_bloque(
    s3, db, make_patient, make_device, make_study
) -> None:
    """Un trigeminismo hasta el borde que sigue con ectópicos seguidos después:
    de una sola vez es un episodio de 11 latidos. El bloque N escribe los 5
    suyos; el N+1 lo informa entero, 11 latidos en 20 s. Estimando por el largo
    que caía afuera (8 de 20 s), el empalme le sumaba 4 y quedaba en 9: los
    latidos no son parejos. Contando los R que el evento no cubría son 6, y
    volver a persistir el mismo hallazgo no suma nada."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    trigeminismo = [285.5, 288.5, 291.5, 294.5, 297.5]
    seguidos = [300.4, 301.2, 302.0, 302.8, 303.6, 304.4]
    episodio = _episodio(1, trigeminismo + seguidos)
    assert episodio.length_samples / SAMPLE_RATE == pytest.approx(19.4, abs=0.1)

    async def persistir(finding: Finding) -> None:
        await ml_persistence.persist_analysis(
            db,
            study,
            _resultado(finding),
            SAMPLE_RATE,
            _scope(lote.id, block_start=300 * SAMPLE_RATE),
        )

    await persistir(_episodio(1, trigeminismo))
    await persistir(episodio)
    (evento,) = await _eventos_del_motor(db, study.id, "morphology_anomaly")
    assert evento.event_metadata["beatCount"] == 11
    await persistir(episodio)
    (evento,) = await _eventos_del_motor(db, study.id, "morphology_anomaly")
    assert evento.event_metadata["beatCount"] == 11


async def test_la_mediana_de_un_episodio_queda_solo_si_el_empalme_no_lo_agranda(
    s3, db, make_patient, make_device, make_study
) -> None:
    """Una mediana no se recompone de dos. El mismo episodio vuelto a informar
    desde el contexto, con el mismo tramo, conserva la suya; si el empalme lo
    agranda, la mediana del primer bloque describiría solo su arranque y se
    saca."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)

    def taquicardia(largo: int, mediana: float) -> Finding:
        finding = _taquicardia(100_000, largo, severa=False, pico=140.0)
        return replace(finding, metadata={**finding.metadata, "medianBpm": mediana})

    async def mediana_tras(finding: Finding) -> float | None:
        await ml_persistence.persist_analysis(
            db, study, _resultado(finding), SAMPLE_RATE, _scope(lote.id, block_start=BLOCK)
        )
        (evento,) = await _eventos_del_motor(db, study.id, "tachycardia")
        return evento.event_metadata.get("medianBpm")

    assert await mediana_tras(taquicardia(20_000, 130.0)) == 130.0
    assert await mediana_tras(taquicardia(20_000, 128.0)) == 130.0
    assert await mediana_tras(taquicardia(30_000, 135.0)) is None


async def test_el_push_es_solo_por_lo_reciente_y_la_alerta_queda_para_el_medico(
    s3, db, make_patient, make_device, make_study
) -> None:
    """Un hallazgo que termina antes de `push_from` es de hace demasiado (el
    backlog de horas, el motor que se pone al día): se escribe con su alerta,
    pero no despierta al paciente."""
    study, (lote, _) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    vieja = _pausa(10_000, 3.2, ECGEventSeverity.CRITICAL)
    reciente = _pausa(40_000, 3.2, ECGEventSeverity.CRITICAL)
    fin_vieja = vieja.start_sample + vieja.length_samples

    _, pushable = await ml_persistence.persist_analysis(
        db,
        study,
        _resultado(vieja),
        SAMPLE_RATE,
        replace(_scope(lote.id), push_from=fin_vieja + 1),
    )
    assert pushable is None
    _, pushable = await ml_persistence.persist_analysis(
        db,
        study,
        _resultado(reciente),
        SAMPLE_RATE,
        replace(_scope(lote.id), push_from=fin_vieja + 1),
    )
    assert pushable is not None
    alertas = (await db.scalars(select(Alert).where(Alert.patient_id == study.patient_id))).all()
    assert len(alertas) == 2


def _corrida(inicio: datetime, horas: float, muestras_por_segundo: float) -> Any:
    """Una corrida que empezó en `inicio` y lleva `horas` grabando a ese ritmo real."""
    inicio_ms = int(inicio.timestamp() * 1000)
    return SimpleNamespace(
        start_sample_index=1_000,
        start_epoch_ms=inicio_ms,
        end_epoch_ms=inicio_ms + int(horas * 3_600_000),
        sample_count=round(horas * 3600 * muestras_por_segundo),
        anchor_matches_boot=True,
    )


def test_push_from_sample_ubica_la_antiguedad_maxima_en_la_corrida(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ml_push_max_age_minutes", 60.0)
    ahora = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    corrida = _corrida(ahora - timedelta(hours=2), 2.0, SAMPLE_RATE)

    # Arrancó hace 2 h: lo que tiene más de 1 h es la primera hora de la corrida.
    assert processing._push_from_sample(corrida, SAMPLE_RATE, ahora) == 1_000 + 3600 * SAMPLE_RATE
    # Una corrida que arrancó hace 10 min es reciente entera.
    corrida = _corrida(ahora - timedelta(minutes=10), 10 / 60, SAMPLE_RATE)
    assert processing._push_from_sample(corrida, SAMPLE_RATE, ahora) == 1_000
    # Apagado: avisa todo.
    monkeypatch.setattr(settings, "ml_push_max_age_minutes", 0.0)
    assert processing._push_from_sample(corrida, SAMPLE_RATE, ahora) == 0


@pytest.mark.parametrize("muestras_por_segundo", [498.7, 492.5, 507.5])
def test_push_from_sample_cuenta_con_la_frecuencia_medida_de_la_corrida(
    monkeypatch, muestras_por_segundo: float
) -> None:
    """El ADS1292R de la placa corre ~0,25 % lento y hasta ±1,5 % con la
    temperatura. Contando a 500 Hz, en una corrida de diez días el corte se
    corría horas: a 498,7 Hz una pausa crítica de hace 15 min ya no avisaba, y
    a 492,5 Hz el corte caía en el futuro y no avisaba nada."""
    monkeypatch.setattr(settings, "ml_push_max_age_minutes", 60.0)
    ahora = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    corrida = _corrida(ahora - timedelta(days=10), 240.0, muestras_por_segundo)
    push_from = processing._push_from_sample(corrida, SAMPLE_RATE, ahora)
    fin = corrida.start_sample_index + corrida.sample_count

    def hace(minutos: float) -> int:
        return fin - round(minutos * 60 * muestras_por_segundo)

    assert hace(15) >= push_from
    assert hace(59) >= push_from
    assert hace(61) < push_from


def test_el_backlog_de_otro_arranque_no_despierta_al_paciente(monkeypatch) -> None:
    """El backlog de un arranque anterior del equipo se ancla a la hora en que
    llegó y no a la que se grabó: horas de señal vieja parecían de recién. Sin
    hora propia, la corrida alerta al médico pero no le manda push a nadie."""
    monkeypatch.setattr(settings, "ml_push_max_age_minutes", 60.0)
    ahora = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    corrida = _corrida(ahora - timedelta(minutes=5), 3.0, SAMPLE_RATE)
    assert processing._push_from_sample(corrida, SAMPLE_RATE, ahora) == 1_000
    corrida.anchor_matches_boot = False
    assert (
        processing._push_from_sample(corrida, SAMPLE_RATE, ahora)
        > corrida.start_sample_index + corrida.sample_count
    )


async def test_la_finalizacion_atribuye_al_lote_que_cubre_el_final_del_bloque(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(90.0)
    cortes = (0, 40, 80, 90)
    lotes = [
        (
            await chaleco.enviar(
                señal[a * SAMPLE_RATE : b * SAMPLE_RATE], flags[a * SAMPLE_RATE : b * SAMPLE_RATE]
            )
        )["batchId"]
        for a, b in zip(cortes, cortes[1:], strict=False)
    ]
    study_id = study.id
    await finalizar(db, monkeypatch, study_id)

    filas = await _calidad(db, study_id)
    por_inicio = {fila.start_sample_index: str(fila.batch_id) for fila in filas}
    # El primer bloque lo completó —y se le atribuye a— el segundo lote; la cola
    # se escribió en el cierre, sin lote que la dispare: es del que la cubre.
    assert por_inicio[0] == lotes[1]
    assert por_inicio[BLOCK] == lotes[2]


async def test_sin_marca_de_lote_en_el_segmento_se_atribuye_al_ultimo_lote(
    s3, db, make_patient, make_device, make_study
) -> None:
    study, (primero, segundo) = await _mundo_con_lotes(db, make_patient, make_device, make_study)
    study.ecg_segments = [
        {"key": "a", "startSampleIndex": 0, "sampleCount": BLOCK, "firstSeq": primero.first_seq},
        {"key": "b", "startSampleIndex": BLOCK, "sampleCount": BLOCK},
    ]

    assert await _attribution_batch(db, study, None, BLOCK) == primero.id
    assert await _attribution_batch(db, study, None, 2 * BLOCK) == segundo.id
    assert await _attribution_batch(db, study, primero, 2 * BLOCK) == primero.id


# --------------------------------------------------------------------------- #
# Totales y finalización
# --------------------------------------------------------------------------- #


async def test_volver_a_correr_el_analisis_no_cuenta_nada_dos_veces(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(150.0)
    study_id = await _analizar_entero(chaleco, study, señal, flags, monkeypatch)

    study = await _estudio(db, study_id)
    totales = dict(study.ml_state["totals"])
    assert totales["analyzedSamples"] == study.samples_count == 150 * SAMPLE_RATE
    assert totales["windows"] == 15
    metricas = study.ml_state["metrics"]
    assert metricas["analyzedHours"] == pytest.approx(150 / 3600, abs=1e-6)
    assert metricas["meanBpm"] == pytest.approx(60.0, abs=1.0)
    assert metricas["beatsSeen"] == study.ml_state["beatsSeen"]
    filas = len(await _calidad(db, study_id))

    repetida = await append_ml_analysis(db, study, None)

    assert repetida == processing.MlPass()
    study = await _estudio(db, study_id)
    assert study.ml_state["totals"] == totales
    assert study.ml_analyzed_samples == study.samples_count
    assert len(await _calidad(db, study_id)) == filas


@pytest.mark.parametrize(("segundos_por_bloque", "bloques"), [(1.0, 1), (0.4, 3)])
async def test_la_pasada_se_corta_antes_del_bloque_que_pasaria_el_presupuesto(
    client,
    s3,
    db,
    monkeypatch,
    bloques_cortos,
    make_patient,
    make_device,
    make_study,
    segundos_por_bloque: float,
    bloques: int,
) -> None:
    """La pasada corre con la fila del estudio tomada, y la ingesta del lote
    siguiente la espera 3 s. El corte suma lo que tardó el bloque anterior
    antes de arrancar otro: mirando solo lo transcurrido, un bloque que
    arrancaba a los 1,9 s terminaba a los 3. El primero corre siempre."""
    monkeypatch.setattr(settings, "ml_enabled", False)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(200.0)
    await chaleco.enviar(señal, flags)
    monkeypatch.setattr(settings, "ml_enabled", True)

    reloj = {"ahora": 0.0}
    real = processing._block_signal

    def _bloque_lento(*args: Any) -> Any:
        reloj["ahora"] += segundos_por_bloque
        return real(*args)

    monkeypatch.setattr(processing, "_block_signal", _bloque_lento)
    monkeypatch.setattr(processing, "time", SimpleNamespace(monotonic=lambda: reloj["ahora"]))
    # `ml_engine` lo levanta para el resto de los tests; acá es lo que se prueba.
    monkeypatch.setattr(processing, "ML_PASS_BUDGET_SECONDS", 1.5)
    study = await _estudio(db, study.id)

    pasada = await append_ml_analysis(db, study, None)

    # 200 s con la corrida abierta: tres bloques listos (el tercero, con su
    # contexto derecho, hasta los 190 s).
    assert study.ml_analyzed_samples == bloques * BLOCK
    assert pasada.pending is (bloques < 3)


async def test_la_finalizacion_analiza_la_cola_antes_de_fundir_morfologias(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """Fundir antes de analizar la cola vería un banco incompleto. Con el tope de
    bloques por pasada en 1, la finalización tiene que dar varias pasadas
    —soltando la fila entre una y otra— y fundir recién al final."""
    monkeypatch.setattr(settings, "ml_analysis_max_blocks_per_pass", 1)
    vistas: list[int] = []
    real = ml_persistence.consolidate_morphologies

    async def _espia(session, target):
        vistas.append(target.ml_analyzed_samples)
        return await real(session, target)

    monkeypatch.setattr(ml_persistence, "consolidate_morphologies", _espia)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(150.0)

    await chaleco.enviar(señal, flags)
    # Un solo bloque por pasada: el lote dejó dos sin analizar.
    assert (await _estudio(db, study.id)).ml_analyzed_samples == BLOCK
    study_id = study.id
    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.samples_count
    assert vistas == [study.samples_count]


async def test_con_el_motor_apagado_el_cursor_no_se_mueve_y_al_prenderlo_se_pone_al_dia(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    monkeypatch.setattr(settings, "ml_enabled", False)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(150.0)
    flags[10 * SAMPLE_RATE : 20 * SAMPLE_RATE] |= FLAG_LEAD_OFF
    study_id = await _analizar_entero(chaleco, study, señal, flags, monkeypatch)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == 0
    assert await _calidad(db, study_id) == []
    capa_a = (await db.scalars(select(ECGEvent).where(ECGEvent.study_id == study_id))).all()
    assert "lead_off" in {evento.event_metadata["kind"] for evento in capa_a}

    monkeypatch.setattr(settings, "ml_enabled", True)
    await finalizar(db, monkeypatch, study_id, cerrar=False)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == study.samples_count
    assert await _calidad(db, study_id)
    assert study.ml_state["totals"]["analyzedSamples"] == study.samples_count


async def test_un_lote_reintentado_vuelve_a_analizar_el_mismo_bloque_sin_duplicar(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """La falla después de persistir hace rollback del cursor junto con todo lo
    demás: el reintento analiza el mismo bloque y deja exactamente una pasada."""
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(70.0)
    study_id = study.id
    body = (
        await post_frames(
            client,
            chaleco.device,
            chaleco.api_key,
            encode_samples(
                [
                    Sample(timestamp_ms=index * STEP_MS, raw_uV=[value], flags=int(flag))
                    for index, (value, flag) in enumerate(
                        zip(to_microvolts(señal), flags, strict=True)
                    )
                ],
                first_seq=0,
                boot_id=0,
                simulated=True,
            ),
        )
    ).json()
    real = ml_persistence.recount_events
    llamadas = {"n": 0}

    async def _falla_una_vez(session, target) -> None:
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("corte después de persistir el bloque")
        await real(session, target)

    monkeypatch.setattr(ml_persistence, "recount_events", _falla_una_vez)
    await process_batch(db, body["batchId"])
    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == 0
    assert await _calidad(db, study_id) == []

    await process_batch(db, body["batchId"])

    lote = await db.get(ECGBatch, body["batchId"])
    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == BLOCK
    assert study.ml_state["totals"]["analyzedSamples"] == BLOCK
    assert {fila.batch_id for fila in await _calidad(db, study_id)} == {lote.id}


def test_una_pausa_sobrevive_a_la_correccion_de_artefactos_en_un_bloque_largo() -> None:
    """Una pausa sinusal de 2,7 s con variabilidad R-R real, sobre un bloque de
    300 s + 60 s. `rpeak_detection.detect_rpeaks` llamaba a
    `nk.ecg_peaks(correct_artifacts=True)`, y la corrección de Kubios leía la
    pausa como latidos perdidos: inventaba R en el medio y la pausa desaparecía.
    Sobre lotes de 15 s casi no había historia para estimar sus umbrales; sobre
    bloques de 300 s sí. La invarianza al tamaño de lote, con pausa incluida, está
    en `test_ml_block_invariance`.
    """
    from app.ml.pipeline import analyze_batch

    rng = np.random.default_rng(3)
    latidos = [t for t in _latidos([(615.0, 60.0)]) if t < 449.6]
    latidos += _latidos([(615.0, 60.0)], desde=latidos[-1] + 2.7)
    # Con variabilidad, como un corazón real: es la que le daba a la corrección
    # de Kubios umbrales con los que leer la pausa como latidos perdidos.
    latidos = [t + float(rng.normal(0.0, 0.03)) for t in latidos]
    señal, flags = _ecg(latidos, 615.0)
    config = build_config(settings, SAMPLE_RATE)
    inicio, contexto = 240 * SAMPLE_RATE, 60 * SAMPLE_RATE

    resultado = analyze_batch(
        señal[inicio : 600 * SAMPLE_RATE],
        flags[inicio : 600 * SAMPLE_RATE],
        start_sample_index=inicio,
        bank=empty_bank(config),
        config=config,
        fold_key="pausa",
        context_samples=contexto,
    )

    assert "pause" in {finding.kind for finding in resultado.findings}
