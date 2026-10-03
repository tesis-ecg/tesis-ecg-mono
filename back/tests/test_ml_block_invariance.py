"""Invarianza al tamaño de lote: la verificación que justifica el cursor.

El puente manda lotes de ~15 s (48 tramas), y el motor no los analiza de a uno:
recorre cada corrida de la línea de tiempo en bloques de
`ml_analysis_block_seconds` con `ml_analysis_context_seconds` de contexto
izquierdo y `ml_analysis_lookahead_seconds` de contexto derecho
(`processing.append_ml_analysis`). Si eso está bien hecho, **los bloques los
define la corrida y no los POST**: el mismo registro tiene que dejar exactamente
lo mismo en la base, llegue en un solo lote o en cuarenta.

Estos tests lo verifican por la ingesta real (`POST /ingest/ecg-frames` →
`process_batch`) y por el cierre real (`complete_study` → `process_study_task`),
con los 300/60/30 s de producción y no con los bloques cortos de
`test_ml_blocks`. La señal mete lo que se rompía con el análisis por lote: una
taquicardia de 90 s sobre el borde de los 300 s (ningún lote de 15 s la ve; el
primer bloque la escribe y el segundo la **empalma**), una pausa de 3,2 s, un
par de pausas a menos de la refractariedad sobre el borde de los 600 s (son
una sola pausa crítica, que el bloque de la cola vuelve a ver desde su
contexto) y un foco ectópico recurrente.

Que los dos tamaños de lote den lo mismo prueba que los bloques los define la
corrida, no que estén bien: con los mismos bordes, un error de empalme sale
igual de los dos lados. Por eso hay un tercer brazo, el registro entero en un
solo bloque, contra el que se comparan los hallazgos de ritmo, sus avisos y
sus latidos, y los totales del estudio: cada borde de bloque tiene que contar
cada latido, cada intervalo y cada ventana como el análisis de corrido.

Fuera de los ectópicos cada latido cae exacto, sin variabilidad R-R. Igual
alcanzó para exponer un defecto del motor: con el foco prematuro en el bloque,
la corrección de artefactos de NeuroKit que usaba `rpeak_detection.detect_rpeaks`
leía la pausa como latidos perdidos, la rellenaba y la pausa no se informaba
con ningún tamaño de lote. Ver el docstring de `detect_rpeaks`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity
from app.modules.ingest import ingest_repository as repo
from tests.ecg_synth import SAMPLE_RATE
from tests.test_ml_blocks import (
    _calidad,
    _ecg,
    _estudio,
    _eventos_del_motor,
    _latidos,
    _mundo,
)
from tests.test_ml_ingest import finalizar

pytestmark = pytest.mark.usefixtures("ml_engine")

BLOCK_S = 300
CONTEXT_S = 60
LOOKAHEAD_S = 30
BLOCK = BLOCK_S * SAMPLE_RATE

#: Diez minutos, el contexto derecho que el segundo bloque espera con la
#: corrida abierta y 15 s más de cola, para que el cierre también analice.
DURACION_S = 645.0
LOTES = 43
#: El primer bloque ve 75 s (más que los 30 de `ml_rhythm_min_seconds`, con su
#: contexto derecho) y la escribe; el segundo la ve entera desde su contexto y
#: la empalma.
TAQUICARDIA_S = (255.0, 345.0)
PAUSA_S = 3.2
#: `(después de, largo)`: la primera, alta, termina antes del borde de 600 s;
#: la segunda, crítica, cierra después. Están a menos de los 10 s de
#: refractariedad: de una sola vez son **una** pausa crítica con un aviso
#: crítico. El segundo bloque las ve las dos (la segunda en su contexto
#: derecho) y el de la cola vuelve a ver el par desde su contexto izquierdo.
PAR_DE_PAUSAS = ((589.0, 2.7), (598.0, 3.4))
#: Lo que manda el puente por POST: 48 tramas de ~140-180 muestras.
LOTE_DEL_PUENTE = 15 * SAMPLE_RATE

_RITMO = ("tachycardia", "bradycardia", "pause")
#: Lo de la metadata de un hallazgo de ritmo que se compara contra el bloque
#: único. `medianBpm` no: un episodio empalmado no la tiene (una mediana no se
#: recompone de dos, `episodes._merge_metadata`). Las frecuencias extremas, con
#: tolerancia: salen de la mediana móvil de 8 latidos, que en el final de un
#: bloque se calcula con menos vecinos.
_METADATA_DE_RITMO = ("pauseSeconds", "durationSeconds", "peakBpm", "minBpm")
_FRECUENCIAS = ("peakBpm", "minBpm")


@pytest.fixture
def bloques_de_produccion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija los 300/60/30 s de producción, por si el entorno los cambia."""
    monkeypatch.setattr(settings, "ml_analysis_block_seconds", float(BLOCK_S))
    monkeypatch.setattr(settings, "ml_analysis_context_seconds", float(CONTEXT_S))
    monkeypatch.setattr(settings, "ml_analysis_lookahead_seconds", float(LOOKAHEAD_S))


# --------------------------------------------------------------------------- #
# Señal
# --------------------------------------------------------------------------- #


def _con_pausa(latidos: list[float], despues_de: float, largo: float) -> tuple[list[float], float]:
    """Una pausa sinusal de `largo` s (entre 2 y 4) después del primer R pasado `despues_de`.

    Después del R que la abre faltan los latidos que caerían adentro y el ritmo
    retoma a la misma frecuencia, corrido lo que sobra del segundo entero.
    Devuelve los latidos y el instante del R que la abre.
    """
    abre = next(t for t in latidos if t > despues_de)
    saltados, corrimiento = int(largo) - 1, largo - int(largo)
    despues = [t + corrimiento for t in latidos if t > abre][saltados:]
    return [t for t in latidos if t <= abre] + despues, abre


def _registro() -> tuple[np.ndarray, np.ndarray, float, int]:
    """645 s a 60 lpm con la taquicardia, las pausas y el foco ectópico.

    Devuelve la señal, los flags, el instante del R que abre la pausa de 3,2 s
    y cuántos ectópicos hay.

    El ectópico es el de `ecg_synth.synth_ecg`: forma distinta (QRS ancho, T
    invertida) **y** prematuro, al 60 % del R-R, con su pausa compensatoria. Cada
    doce latidos, lejos de la taquicardia y de las pausas para que ninguna
    dependa de él.
    """
    inicio, fin = TAQUICARDIA_S
    latidos = _latidos([(inicio, 60.0), (fin - inicio, 130.0), (DURACION_S - fin, 60.0)])
    latidos, abre = _con_pausa(latidos, 449.0, PAUSA_S)
    for despues_de, largo in PAR_DE_PAUSAS:
        latidos, _ = _con_pausa(latidos, despues_de, largo)
    ectopicos = frozenset(
        indice
        for indice, instante in enumerate(latidos)
        if indice % 12 == 11
        and not inicio - 10.0 < instante < fin + 10.0
        and not abre - 10.0 < instante < abre + 10.0
        and not PAR_DE_PAUSAS[0][0] - 10.0 < instante
    )
    latidos = [t - 0.4 if indice in ectopicos else t for indice, t in enumerate(latidos)]
    señal, flags = _ecg(latidos, DURACION_S, ectopicos=ectopicos)
    return señal, flags, abre, len(ectopicos)


# --------------------------------------------------------------------------- #
# Ingesta y huella
# --------------------------------------------------------------------------- #

DespuesDeCadaLote = Callable[[uuid.UUID], Awaitable[None]]


async def _ingerir_y_cerrar(
    client: Any,
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
    fabricas: tuple[Any, Any, Any],
    señal: np.ndarray,
    flags: np.ndarray,
    lotes: int,
    *,
    despues_de_cada_lote: DespuesDeCadaLote | None = None,
) -> uuid.UUID:
    """Un estudio nuevo, la señal en `lotes` POST contiguos y el cierre real.

    `np.array_split` reparte las muestras en lotes que difieren en una como
    mucho; `_Chaleco` mantiene `seq` y `t0Ms` contiguos, así que todo es una
    sola corrida.
    """
    chaleco, study = await _mundo(client, db, *fabricas)
    study_id = study.id
    for tramo in np.array_split(np.arange(señal.size), lotes):
        await chaleco.enviar(señal[tramo], flags[tramo])
        if despues_de_cada_lote is not None:
            await despues_de_cada_lote(study_id)
    await finalizar(db, monkeypatch, study_id)
    return study_id


async def _huella(db: Any, study_id: uuid.UUID) -> dict[str, Any]:
    """Todo lo que el motor dejó en la base, sin lo que depende del lote.

    Queda afuera solo lo que es por construcción del lote o del estudio: el
    `batch_id` de cada fila (se atribuye al lote que disparó el bloque), los
    `id` y el `studyId` de la metadata.
    """
    study = await _estudio(db, study_id)
    eventos = await _eventos_del_motor(db, study_id)
    avisos = (
        await db.execute(
            select(Alert, ECGEvent)
            .join(ECGEvent, Alert.event_id == ECGEvent.id)
            .where(ECGEvent.study_id == study_id)
        )
    ).all()
    return {
        "cursor": study.ml_analyzed_samples,
        "muestras": study.samples_count,
        "totales": study.ml_state["totals"],
        "metricas": study.ml_state["metrics"],
        "eventos": sorted(
            (
                evento.event_metadata["kind"],
                evento.event_metadata["startSampleIndex"],
                evento.event_metadata["sampleCount"],
                evento.event_metadata.get("clusterId"),
                evento.event_metadata.get("beatCount"),
                evento.severity.value,
                evento.event_type.value,
                evento.dedupe_key,
                evento.timestamp_in_recording,
                evento.duration_seconds,
                evento.confidence_score,
                repr(
                    sorted(
                        (clave, valor)
                        for clave, valor in evento.event_metadata.items()
                        if clave != "studyId"
                    )
                ),
            )
            for evento in eventos
        ),
        "ritmo": sorted(
            (
                evento.event_metadata["kind"],
                evento.event_metadata["startSampleIndex"],
                evento.event_metadata["sampleCount"],
                evento.severity.value,
                tuple(evento.event_metadata.get(clave) for clave in _METADATA_DE_RITMO),
            )
            for evento in eventos
            if evento.event_metadata["kind"] in _RITMO
        ),
        "calidad": [
            (
                fila.start_sample_index,
                fila.sample_count,
                fila.level.value,
                fila.reason,
                fila.window_count,
                fila.metrics,
            )
            for fila in await _calidad(db, study_id)
        ],
        "avisos": sorted(
            (
                evento.event_metadata["kind"],
                evento.event_metadata["startSampleIndex"],
                aviso.kind,
                aviso.severity.value,
                aviso.message,
            )
            for aviso, evento in avisos
        ),
    }


def _de_tipo(huella: dict[str, Any], kind: str) -> list[tuple[Any, ...]]:
    return [evento for evento in huella["eventos"] if evento[0] == kind]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_el_mismo_registro_en_un_lote_o_en_cuarenta_deja_lo_mismo_en_la_base(
    client,
    s3,
    db,
    monkeypatch,
    bloques_de_produccion,
    make_patient,
    make_device,
    make_study,
) -> None:
    """Un POST de 645 s contra cuarenta y tres de ~15 s: mismos eventos (con su
    `clusterId`, `beatCount`, severidad y metadata entera), mismas filas de
    calidad, mismos avisos, mismos totales y métricas del estudio y el cursor al
    final de la señal.

    El primer bloque ve 45 s de la taquicardia y la escribe; el segundo la ve
    entera con su contexto y la empalma con ese evento. Con los cuarenta lotes
    eso se ve pasar: una taquicardia desde que el cursor cubre el primer bloque,
    y sigue siendo una cuando cubre el segundo.
    """
    señal, flags, abre, n_ectopicos = _registro()
    fabricas = (make_patient, make_device, make_study)
    recorrido: list[tuple[int, int]] = []

    async def mirar(study_id: uuid.UUID) -> None:
        study = await _estudio(db, study_id)
        taquicardias = await _eventos_del_motor(db, study_id, "tachycardia")
        recorrido.append((study.ml_analyzed_samples, len(taquicardias)))

    un_lote = await _huella(
        db, await _ingerir_y_cerrar(client, db, monkeypatch, fabricas, señal, flags, 1)
    )
    por_lotes = await _huella(
        db,
        await _ingerir_y_cerrar(
            client, db, monkeypatch, fabricas, señal, flags, LOTES, despues_de_cada_lote=mirar
        ),
    )

    # Clave por clave, para que una falla diga qué se movió.
    for clave in un_lote:
        assert por_lotes[clave] == un_lote[clave], clave

    assert un_lote["cursor"] == un_lote["muestras"] == señal.size

    # El cursor avanzó de a bloques enteros y la cola esperó al cierre.
    assert sorted({cursor for cursor, _ in recorrido}) == [0, BLOCK, 2 * BLOCK]
    assert all(n == (0 if cursor == 0 else 1) for cursor, n in recorrido), recorrido

    # Una sola taquicardia, escrita por el primer bloque y empalmada por el segundo.
    ((_, inicio, largo, *_),) = _de_tipo(un_lote, "tachycardia")
    assert inicio / SAMPLE_RATE == pytest.approx(TAQUICARDIA_S[0], abs=1.5)
    assert (inicio + largo) / SAMPLE_RATE == pytest.approx(TAQUICARDIA_S[1], abs=1.5)
    assert inicio < BLOCK < inicio + largo

    # La pausa de 3,2 s, crítica y con un solo aviso. El par sobre el borde de
    # la cola es una sola pausa crítica con un solo aviso crítico.
    pausa, par = _de_tipo(un_lote, "pause")
    assert pausa[1] / SAMPLE_RATE == pytest.approx(abre, abs=0.1)
    assert pausa[2] / SAMPLE_RATE == pytest.approx(PAUSA_S, abs=0.05)
    assert pausa[5] == par[5] == ECGEventSeverity.CRITICAL.value
    assert par[1] < 2 * BLOCK < par[1] + par[2]
    assert [aviso[1:4] for aviso in un_lote["avisos"] if aviso[0] == "pause"] == [
        (pausa[1], "pause", AlertSeverity.CRITICAL.value),
        (par[1], "pause", AlertSeverity.CRITICAL.value),
    ]

    # El foco recurrente tiene su encabezado de estudio y sus episodios. Hay un
    # segundo encabezado, el de los latidos de la taquicardia: a 130 lpm la T
    # del latido anterior entra en la ventana del siguiente y la forma es otra.
    focos = [
        item
        for item in _de_tipo(un_lote, "recurrent_morphology")
        if item[4] == pytest.approx(n_ectopicos, rel=0.1)
    ]
    assert len(focos) == 1, un_lote["eventos"]
    assert _de_tipo(un_lote, "morphology_anomaly")

    # El tercer brazo: el registro entero en un solo bloque, que analiza el
    # cierre. Los bordes de bloque no pueden cambiar ningún hallazgo de ritmo,
    # ni su aviso, ni cuántos latidos tiene.
    monkeypatch.setattr(settings, "ml_analysis_block_seconds", DURACION_S + 60.0)
    de_una_vez = await _huella(
        db, await _ingerir_y_cerrar(client, db, monkeypatch, fabricas, señal, flags, 1)
    )
    assert [aviso for aviso in un_lote["avisos"] if aviso[0] in _RITMO] == [
        aviso for aviso in de_una_vez["avisos"] if aviso[0] in _RITMO
    ]
    for empalmado, referencia in zip(_ritmo(un_lote), _ritmo(de_una_vez), strict=True):
        exactos, frecuencias = empalmado[:-1], empalmado[-1]
        assert exactos == referencia[:-1]
        for clave in _FRECUENCIAS:
            assert frecuencias[clave] == pytest.approx(referencia[-1][clave], rel=0.01), clave
    # Los latidos de un episodio empalmado son la unión de los que vio cada
    # bloque (`ml_persistence._stitch`), exactos: ni el par de pausas ni la
    # taquicardia cuentan dos veces el R que dos bloques comparten.
    assert [
        evento[4] for evento in _de_tipo(un_lote, "pause") + _de_tipo(un_lote, "tachycardia")
    ] == [
        evento[4] for evento in _de_tipo(de_una_vez, "pause") + _de_tipo(de_una_vez, "tachycardia")
    ]
    # Y los totales del estudio: cada latido, cada intervalo NN, cada
    # diferencia sucesiva y cada ventana, contados una vez y como los cuenta el
    # análisis de corrido. Sin contexto derecho, un borde sobre un QRS sumaba un
    # latido fantasma y uno que terminaba en un ectópico lo metía en el RMSSD.
    por_bloques, entero = un_lote["totales"], de_una_vez["totales"]
    assert set(por_bloques) == set(entero)
    for clave, valor in entero.items():
        assert por_bloques[clave] == pytest.approx(valor, rel=1e-9), clave


def _ritmo(huella: dict[str, Any]) -> list[tuple[Any, ...]]:
    """`(kind, inicio, largo, severidad, metadata exacta, frecuencias)` de los de ritmo."""
    filas = []
    for kind, inicio, largo, severidad, valores in huella["ritmo"]:
        metadata = dict(zip(_METADATA_DE_RITMO, valores, strict=True))
        frecuencias = {clave: metadata.pop(clave) for clave in _FRECUENCIAS}
        filas.append((kind, inicio, largo, severidad, metadata, frecuencias))
    return filas


async def test_un_hueco_real_de_grabacion_separa_las_corridas_y_ningun_hallazgo_lo_cruza(
    client,
    s3,
    db,
    monkeypatch,
    bloques_de_produccion,
    make_patient,
    make_device,
    make_study,
) -> None:
    """Dos corridas, separadas por 30 s en que el equipo no grabó. En el buffer
    empaquetado son contiguas, y ahí la taquicardia del final de la primera y la
    del principio de la segunda suman 40 s. Pero cada una dura 20 s: ningún
    bloque cruza el borde, así que no hay taquicardia ahí.

    El control es el mismo buffer en una sola corrida: ahí sí hay una
    taquicardia, y cruza el borde. La segunda corrida además tiene una
    taquicardia propia de 40 s, para que el silencio del borde no sea un motor
    que dejó de ver.
    """
    primera_s, segunda_s = 330.0, 120.0
    primera, primeros_flags = _ecg(_latidos([(310.0, 60.0), (20.0, 130.0)]), primera_s)
    segunda, segundos_flags = _ecg(
        _latidos([(20.0, 130.0), (40.0, 60.0), (40.0, 130.0), (20.0, 60.0)]),
        segunda_s,
        seed=11,
    )
    borde = int(primera_s * SAMPLE_RATE)
    fin = borde + int(segunda_s * SAMPLE_RATE)
    fabricas = (make_patient, make_device, make_study)

    async def ingerir(hueco_s: float) -> uuid.UUID:
        chaleco, study = await _mundo(client, db, *fabricas)
        study_id = study.id
        for señal, flags, salto in (
            (primera, primeros_flags, 0),
            (segunda, segundos_flags, int(hueco_s * SAMPLE_RATE)),
        ):
            tramos = np.array_split(np.arange(señal.size), round(señal.size / LOTE_DEL_PUENTE))
            for indice, tramo in enumerate(tramos):
                # El salto de `t0Ms` va en el primer lote de la segunda corrida.
                await chaleco.enviar(
                    señal[tramo], flags[tramo], salto_muestras=0 if indice else salto
                )
        await finalizar(db, monkeypatch, study_id)
        return study_id

    con_hueco = await ingerir(30.0)
    sin_hueco = await ingerir(0.0)

    corridas = await repo.list_timeline_segments(db, con_hueco)
    assert [(c.start_sample_index, c.sample_count) for c in corridas] == [
        (0, borde),
        (borde, fin - borde),
    ]
    assert len(await repo.list_timeline_segments(db, sin_hueco)) == 1
    study = await _estudio(db, con_hueco)
    assert study.ml_analyzed_samples == study.samples_count == fin

    # Nada del motor atraviesa el borde, ni un evento ni una fila de calidad. Los
    # encabezados de estudio quedan afuera a propósito: resumen un cluster de
    # todo el estudio (del primer latido al último), no son un episodio.
    for evento in await _eventos_del_motor(db, con_hueco):
        if evento.event_metadata["scope"] == "study":
            continue
        inicio = evento.event_metadata["startSampleIndex"]
        final = inicio + evento.event_metadata["sampleCount"]
        assert final <= borde or inicio >= borde, evento.event_metadata
    for fila in await _calidad(db, con_hueco):
        assert fila.start_sample_index + fila.sample_count <= borde or (
            fila.start_sample_index >= borde
        )

    # La única taquicardia es la propia de la segunda corrida.
    (propia,) = await _eventos_del_motor(db, con_hueco, "tachycardia")
    inicio = propia.event_metadata["startSampleIndex"]
    assert (inicio - borde) / SAMPLE_RATE == pytest.approx(60.0, abs=1.5)
    assert propia.event_metadata["durationSeconds"] == pytest.approx(40.0, abs=2.0)

    # El control: sin el hueco, las dos mitades son una taquicardia que cruza.
    cruzadas = [
        evento
        for evento in await _eventos_del_motor(db, sin_hueco, "tachycardia")
        if evento.event_metadata["startSampleIndex"]
        < borde
        < evento.event_metadata["startSampleIndex"] + evento.event_metadata["sampleCount"]
    ]
    assert len(cruzadas) == 1
    assert cruzadas[0].event_metadata["durationSeconds"] == pytest.approx(40.0, abs=2.0)
