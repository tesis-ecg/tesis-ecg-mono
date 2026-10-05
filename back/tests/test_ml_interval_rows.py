"""Las mediciones de intervalos como dato de investigación (`ecg_interval_measurement`).

Lo que se verifica es el contrato de la fase: el pipeline mide QT, QTc y
amplitud R de la **parte nueva** de cada bloque (`pipeline._measure_intervals`)
detrás de `ml_interval_measurements_enabled`; la persistencia deja una fila por
bloque medido, idempotente por `(study_id, start_sample_index)`, y ninguna por
un bloque sin medición; apagado no se calcula nada; medir no cambia ni un
hallazgo, ni un total, ni el banco; un error de la medición no tumba el bloque;
ninguna API expone un campo de intervalos; y el export para la tesis no saca
datos del paciente.

La medición en sí —el delineador, sus guardas, la evidencia de la QT
Database— está en `test_ml_intervals`.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_interval_measurement import ECGIntervalMeasurement
from app.db.models.patient import Patient
from app.ml import pipeline
from app.ml.decompression import FLAG_LEAD_OFF
from app.ml.intervals import IntervalMeasurement
from app.ml.pipeline import PIPELINE_VERSION, PipelineResult, build_config, empty_bank
from app.modules.ingest import ml_persistence
from app.scripts.export_interval_measurements import COLUMNS, fetch_rows, write_csv
from tests.ecg_synth import SAMPLE_RATE, synth_ecg
from tests.test_ml_blocks import BLOCK, _calidad, _estudio, _mundo, _sinusal, bloques_cortos
from tests.test_ml_ingest import finalizar

__all__ = ["bloques_cortos"]

pytestmark = pytest.mark.usefixtures("ml_engine")

CONTEXT = 60 * SAMPLE_RATE
LOOKAHEAD = 30 * SAMPLE_RATE


def _config(**overrides: Any) -> pipeline.PipelineConfig:
    return replace(build_config(settings, SAMPLE_RATE), **overrides)


def _analizar(
    señal: np.ndarray,
    flags: np.ndarray,
    config: pipeline.PipelineConfig,
    *,
    desde: int = 0,
    hasta: int | None = None,
    contexto: int = 0,
    derecho: int = 0,
) -> PipelineResult:
    """Un bloque como lo arma `append_ml_analysis`: lee `[desde, hasta)` con sus contextos."""
    fin = señal.size if hasta is None else hasta
    return pipeline.analyze_batch(
        señal[desde:fin],
        flags[desde:fin],
        start_sample_index=desde,
        bank=empty_bank(config),
        config=config,
        fold_key=f"{desde}:{fin}",
        context_samples=contexto,
        lookahead_samples=derecho,
    )


async def _filas(db, study_id: uuid.UUID) -> list[ECGIntervalMeasurement]:
    return list(
        (
            await db.scalars(
                select(ECGIntervalMeasurement)
                .where(ECGIntervalMeasurement.study_id == study_id)
                .order_by(ECGIntervalMeasurement.start_sample_index)
            )
        ).all()
    )


def _medicion(**overrides: Any) -> IntervalMeasurement:
    valores: dict[str, Any] = {
        "beats": 55,
        "candidate_beats": 58,
        "coverage_ratio": 55 / 58,
        "qt_ms": 340.0,
        "qtc_ms": 340.0,
        "r_amplitude_mv": 1.0,
        "heart_rate_bpm": 60.0,
        "candidate_heart_rate_bpm": 60.0,
    }
    valores.update(overrides)
    return IntervalMeasurement(**valores)


# --------------------------------------------------------------------------- #
# El pipeline
# --------------------------------------------------------------------------- #


def test_el_bloque_mide_solo_los_latidos_de_su_parte_nueva() -> None:
    """Con contexto a los dos lados, los candidatos son exactamente los R de la parte nueva.

    El primero de la parte nueva entra: su R-R previo sale del contexto
    izquierdo, que es señal buena. Los del contexto no: los midió el bloque
    anterior, y medirlos de nuevo los contaría dos veces en la tesis.
    """
    ecg = synth_ecg(390.0, bpm=70.0, seed=3)
    resultado = _analizar(ecg.signal_mv, ecg.flags, _config(), contexto=CONTEXT, derecho=LOOKAHEAD)

    nuevos = int(np.count_nonzero((ecg.rpeaks >= CONTEXT) & (ecg.rpeaks < 360 * SAMPLE_RATE)))
    assert resultado.intervals is not None
    assert resultado.intervals.candidate_beats == nuevos
    assert resultado.intervals.beats == nuevos
    assert resultado.intervals.experimental is True
    assert resultado.intervals.qrs_ms is None
    assert 300.0 < resultado.intervals.qtc_ms < 450.0


def test_cada_latido_entra_en_un_solo_bloque() -> None:
    """Dos bloques consecutivos cuentan los mismos candidatos que el registro de una vez.

    Es la invarianza que importa para la tesis: sumar los latidos de las filas
    de un estudio tiene que dar los latidos del estudio, sin depender de dónde
    cayó el borde.
    """
    ecg = synth_ecg(630.0, bpm=70.0, seed=3)
    config = _config()
    borde, fin = 300 * SAMPLE_RATE, 600 * SAMPLE_RATE
    entero = _analizar(ecg.signal_mv, ecg.flags, config, derecho=LOOKAHEAD)
    primero = _analizar(
        ecg.signal_mv, ecg.flags, config, hasta=borde + LOOKAHEAD, derecho=LOOKAHEAD
    )
    segundo = _analizar(
        ecg.signal_mv, ecg.flags, config, desde=borde - CONTEXT, contexto=CONTEXT, derecho=LOOKAHEAD
    )

    assert entero.intervals and primero.intervals and segundo.intervals
    assert primero.intervals.candidate_beats + segundo.intervals.candidate_beats == (
        entero.intervals.candidate_beats
    )
    assert entero.intervals.candidate_beats == int(np.count_nonzero(ecg.rpeaks[1:] < fin))


def test_con_ectopia_se_miden_solo_los_sinusales_con_vecinos_sinusales() -> None:
    """La máscara dominante sale de la asignación de morfología del bloque.

    Un ventricular cada cinco latidos: de cada cinco quedan dos (el ectópico,
    el pre- y el post-ectópico no se miden). Sin la máscara se medirían los
    cinco, y con bigeminismo no queda ninguno: el bloque sale sin medición.
    """
    ecg = synth_ecg(390.0, bpm=70.0, ectopic_every=5, seed=3)
    resultado = _analizar(ecg.signal_mv, ecg.flags, _config(), contexto=CONTEXT, derecho=LOOKAHEAD)
    nuevos = int(np.count_nonzero((ecg.rpeaks >= CONTEXT) & (ecg.rpeaks < 360 * SAMPLE_RATE)))
    assert resultado.intervals is not None
    assert resultado.intervals.candidate_beats == pytest.approx(nuevos * 2 / 5, abs=2)

    bigeminismo = synth_ecg(390.0, bpm=70.0, ectopic_every=2, seed=3)
    assert (
        _analizar(
            bigeminismo.signal_mv,
            bigeminismo.flags,
            _config(),
            contexto=CONTEXT,
            derecho=LOOKAHEAD,
        ).intervals
        is None
    )


def test_medir_no_cambia_ningun_hallazgo_ni_total_ni_el_banco() -> None:
    ecg = synth_ecg(390.0, bpm=70.0, ectopic_every=7, seed=5)
    prendida = _analizar(ecg.signal_mv, ecg.flags, _config(), contexto=CONTEXT, derecho=LOOKAHEAD)
    apagada = _analizar(
        ecg.signal_mv, ecg.flags, _config(intervals=None), contexto=CONTEXT, derecho=LOOKAHEAD
    )

    assert prendida.intervals is not None
    assert apagada.intervals is None
    assert prendida.findings == apagada.findings
    assert prendida.quality_intervals == apagada.quality_intervals
    assert prendida.totals == apagada.totals
    assert prendida.metrics == apagada.metrics
    assert prendida.bank.beats_seen == apagada.bank.beats_seen
    assert [t.count for t in prendida.bank.templates] == [t.count for t in apagada.bank.templates]


def test_con_la_medicion_apagada_no_se_calcula_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ml_interval_measurements_enabled", False)
    llamadas: list[object] = []
    monkeypatch.setattr(pipeline, "_measure_intervals", lambda *a, **k: llamadas.append(a))
    config = build_config(settings, SAMPLE_RATE)
    ecg = synth_ecg(90.0, bpm=70.0, seed=3)

    assert config.intervals is None
    assert _analizar(ecg.signal_mv, ecg.flags, config).intervals is None
    assert llamadas == []


def test_un_error_de_la_medicion_no_tumba_el_bloque(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un dato de investigación no puede trabar el cursor del motor.

    Si la medición levantara, la pasada fallaría entera, el cursor no avanzaría
    y `ML_ANALYSIS_PENDING` bloquearía el informe de ese estudio.
    """
    ecg = synth_ecg(90.0, bpm=70.0, seed=3)
    sano = _analizar(ecg.signal_mv, ecg.flags, _config())

    def _rompe(*args: object, **kwargs: object) -> None:
        raise RuntimeError("defecto propio")

    monkeypatch.setattr(pipeline, "measure_intervals", _rompe)
    roto = _analizar(ecg.signal_mv, ecg.flags, _config())

    assert sano.intervals is not None
    assert roto.intervals is None
    assert roto.findings == sano.findings
    assert roto.totals == sano.totals


def test_un_bloque_sin_latidos_buenos_no_filtra_ni_delinea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llamadas: list[object] = []
    monkeypatch.setattr(pipeline, "_amplitude_signal", lambda *a, **k: llamadas.append(a))
    plano = np.zeros(90 * SAMPLE_RATE, dtype=np.float32)

    resultado = _analizar(plano, np.zeros(plano.size, dtype=np.uint8), _config())

    assert resultado.intervals is None
    assert llamadas == []


def test_la_senal_de_amplitud_puentea_el_riel_antes_del_notch() -> None:
    """El notch sobre un escalón de 400 mV oscila ~1 s: caería encima de los R siguientes.

    Con el riel puenteado, la señal de amplitud un poco después del electrodo
    despegado es la misma que sin el despegue.
    """
    ecg = synth_ecg(60.0, bpm=70.0, seed=3)
    limpia = pipeline._amplitude_signal(ecg.signal_mv, ecg.flags, SAMPLE_RATE, 50.0)
    señal, flags = ecg.signal_mv.copy(), ecg.flags.copy()
    riel = slice(20 * SAMPLE_RATE, 25 * SAMPLE_RATE)
    señal[riel] = 400.0
    flags[riel] |= FLAG_LEAD_OFF
    despegada = pipeline._amplitude_signal(señal, flags, SAMPLE_RATE, 50.0)

    despues = slice(25 * SAMPLE_RATE + SAMPLE_RATE // 5, 27 * SAMPLE_RATE)
    assert np.max(np.abs(despegada[despues] - limpia[despues])) < 0.05
    # Sin el puente, la oscilación es de decenas de milivoltios.
    sin_puente = pipeline._amplitude_signal(señal, np.zeros_like(flags), SAMPLE_RATE, 50.0)
    assert np.max(np.abs(sin_puente[despues] - limpia[despues])) > 1.0


# --------------------------------------------------------------------------- #
# La persistencia, por la ingesta real
# --------------------------------------------------------------------------- #


async def test_cada_bloque_medido_deja_una_fila_y_el_que_no_se_midio_ninguna(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    """Bloques de 60 s: dos se miden y la cola de 15 s, con 15 latidos, no.

    La cola se analiza (tiene su calidad) pero no llega a `min_beats`: no deja
    fila. Una fila con todo en NULL no diría nada que la falta de fila no diga.
    """
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(135.0)
    await chaleco.enviar(señal[: 90 * SAMPLE_RATE], flags[: 90 * SAMPLE_RATE])
    await chaleco.enviar(señal[90 * SAMPLE_RATE :], flags[90 * SAMPLE_RATE :])
    study_id = study.id
    await finalizar(db, monkeypatch, study_id)

    study = await _estudio(db, study_id)
    assert study.ml_analyzed_samples == 135 * SAMPLE_RATE
    assert any(fila.start_sample_index == 2 * BLOCK for fila in await _calidad(db, study_id))
    filas = await _filas(db, study_id)
    assert [(fila.start_sample_index, fila.sample_count) for fila in filas] == [
        (0, BLOCK),
        (BLOCK, BLOCK),
    ]
    for fila in filas:
        assert fila.experimental is True
        assert fila.qrs_ms is None
        assert fila.method == "prominence"
        assert fila.model_version == PIPELINE_VERSION
        assert 30 <= fila.beats <= fila.candidate_beats <= 60
        assert fila.heart_rate_bpm == pytest.approx(60.0, abs=1.0)
        lote = await db.get(ECGBatch, fila.batch_id)
        assert lote is not None and lote.study_id == study_id


async def test_persistir_dos_veces_el_mismo_bloque_no_duplica_la_fila(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """Idempotente por `(study_id, start_sample_index)`: la segunda escritura no pisa la primera."""
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(20.0)
    body = await chaleco.enviar(señal, flags)
    scope = ml_persistence.BlockScope(
        batch_id=uuid.UUID(body["batchId"]),
        boot_id=0,
        run_start=0,
        read_start=0,
        block_start=0,
        block_end=60 * SAMPLE_RATE,
    )

    def resultado(medicion: IntervalMeasurement | None) -> PipelineResult:
        return PipelineResult(
            quality_intervals=(),
            findings=(),
            bank=empty_bank(build_config(settings, SAMPLE_RATE)),
            metrics={},
            model_version=PIPELINE_VERSION,
            intervals=medicion,
        )

    await ml_persistence.persist_analysis(db, study, resultado(_medicion()), SAMPLE_RATE, scope)
    await ml_persistence.persist_analysis(
        db, study, resultado(_medicion(qtc_ms=999.0)), SAMPLE_RATE, scope
    )
    # Un bloque sin medición no escribe nada, tampoco en otra posición.
    otro = replace(scope, block_start=60 * SAMPLE_RATE, block_end=120 * SAMPLE_RATE)
    await ml_persistence.persist_analysis(db, study, resultado(None), SAMPLE_RATE, otro)

    (fila,) = await _filas(db, study.id)
    assert fila.qtc_ms == 340.0
    assert fila.candidate_heart_rate_bpm == 60.0


async def test_con_la_medicion_apagada_la_ingesta_no_escribe_filas(
    client, s3, db, monkeypatch, bloques_cortos, make_patient, make_device, make_study
) -> None:
    monkeypatch.setattr(settings, "ml_interval_measurements_enabled", False)
    llamadas: list[object] = []
    monkeypatch.setattr(pipeline, "measure_intervals", lambda *a, **k: llamadas.append(a))
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(135.0)
    await chaleco.enviar(señal, flags)
    study_id = study.id
    await finalizar(db, monkeypatch, study_id)

    assert await _calidad(db, study_id)  # el motor corrió
    assert await _filas(db, study_id) == []
    assert llamadas == []


async def test_borrar_los_lotes_del_estudio_se_lleva_sus_filas(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """`ON DELETE CASCADE`: un dato derivado no traba el borrado de un estudio (los seeds)."""
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(20.0)
    body = await chaleco.enviar(señal, flags)
    lote = uuid.UUID(body["batchId"])
    scope = ml_persistence.BlockScope(
        batch_id=lote, boot_id=0, run_start=0, read_start=0, block_start=0, block_end=20 * 500
    )
    resultado = PipelineResult(
        quality_intervals=(),
        findings=(),
        bank=empty_bank(build_config(settings, SAMPLE_RATE)),
        metrics={},
        model_version=PIPELINE_VERSION,
        intervals=_medicion(beats=20, candidate_beats=20, coverage_ratio=1.0),
    )
    await ml_persistence.persist_analysis(db, study, resultado, SAMPLE_RATE, scope)
    assert len(await _filas(db, study.id)) == 1

    await db.execute(delete(ECGBatch).where(ECGBatch.id == lote))

    assert await _filas(db, study.id) == []


# --------------------------------------------------------------------------- #
# Nada lo expone
# --------------------------------------------------------------------------- #

#: Lo que delataría un campo de intervalos en un schema, en snake y en camel.
_CAMPOS_DE_INTERVALOS = (
    "qtc",
    "qt_ms",
    "qtms",
    "r_amplitude",
    "ramplitude",
    "qrs_ms",
    "qrsms",
    "interval_measurement",
    "intervalmeasurement",
)


def _claves(nodo: Any) -> set[str]:
    if isinstance(nodo, dict):
        return {str(clave).lower() for clave in nodo} | {
            clave for valor in nodo.values() for clave in _claves(valor)
        }
    if isinstance(nodo, list):
        return {clave for valor in nodo for clave in _claves(valor)}
    return set()


def test_ningun_schema_de_la_api_expone_los_intervalos() -> None:
    """Ni el contrato commiteado (`openapi.json`, del que sale el front) ni el de la app viva."""
    from app.main import app

    commiteado = json.loads((Path(__file__).resolve().parents[1] / "openapi.json").read_text())
    for contrato in (commiteado, app.openapi()):
        claves = _claves(contrato)
        expuestas = sorted(
            clave for clave in claves for campo in _CAMPOS_DE_INTERVALOS if campo in clave
        )
        assert expuestas == []
        # Tampoco una ruta.
        assert not [ruta for ruta in contrato["paths"] if "interval" in ruta.lower()]


# --------------------------------------------------------------------------- #
# El export para la tesis
# --------------------------------------------------------------------------- #


async def test_el_export_saca_una_fila_por_bloque_sin_datos_del_paciente(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    señal, flags = _sinusal(20.0)
    body = await chaleco.enviar(señal, flags)
    scope = ml_persistence.BlockScope(
        batch_id=uuid.UUID(body["batchId"]),
        boot_id=0,
        run_start=0,
        read_start=0,
        block_start=0,
        block_end=60 * SAMPLE_RATE,
    )
    for inicio in (60 * SAMPLE_RATE, 0):
        resultado = PipelineResult(
            quality_intervals=(),
            findings=(),
            bank=empty_bank(build_config(settings, SAMPLE_RATE)),
            metrics={},
            model_version=PIPELINE_VERSION,
            intervals=_medicion(qt_ms=340.04, qtc_ms=351.26),
        )
        bloque = replace(scope, block_start=inicio, block_end=inicio + 60 * SAMPLE_RATE)
        await ml_persistence.persist_analysis(db, study, resultado, SAMPLE_RATE, bloque)

    filas = await fetch_rows(db, study.id)
    salida = io.StringIO()
    assert write_csv(filas, salida) == 2

    lineas = salida.getvalue().splitlines()
    assert lineas[0].split(",") == list(COLUMNS)
    # En orden de registro, con el tiempo de registro y sin redondeos raros.
    assert [fila["start_s"] for fila in filas] == [0.0, 60.0]
    assert filas[0]["qtc_ms"] == 351.3
    assert filas[0]["qrs_ms"] == ""
    # Ni un dato que identifique al paciente: el estudio es la única referencia.
    paciente = {"patient", "dni", "name", "first_name", "last_name", "medical_record", "email"}
    assert not [col for col in COLUMNS for dato in paciente if dato in col]
    # Ni una fecha: la hora de la fila fecharía el monitoreo del paciente.
    assert not [col for col in COLUMNS if col.endswith(("_at", "_date", "_time", "timestamp"))]
    fecha = re.compile(r"\d{4}-\d{2}-\d{2}")
    assert not [v for fila in filas for v in fila.values() if fecha.search(str(v))]
    assert await fetch_rows(db, uuid.uuid4()) == []

    # Un paciente dado de baja sale del export con sus estudios.
    paciente_db = await db.get(Patient, study.patient_id)
    assert paciente_db is not None
    paciente_db.deleted_at = datetime.now(UTC)
    await db.flush()
    assert await fetch_rows(db, study.id) == []
