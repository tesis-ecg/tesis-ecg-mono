"""La frecuencia cardíaca no es una morfología.

Un latido sinusal normal de una escalera, a 150 lpm, no tiene la forma de uno en
reposo: la ventana de ±250 ms trae la T del latido anterior y la P del
siguiente, y su propia T llega antes porque el QT se acorta. Medido en vivo
(estudio sintético del e2e, 60 s a 155 lpm): 14 episodios de "morfología
atípica" y un segundo foco recurrente con el 9 % de la carga hecho de latidos
normales. Con un paciente de verdad, cada escalera.

Estos tests fijan lo que lo corrige —el score mira cada latido a su frecuencia
(`morphology.dissimilarity_to_dominant` con `BeatRate`) y un foco tiene que haber
puntuado (`morphology.is_recurrent`)— y lo que eso no puede romper: los
ectópicos se siguen viendo, adentro y afuera de la taquicardia, el banco no
cuenta dos veces un bloque y los `cluster_id` no se renumeran.

Tres generadores, porque cada uno tiene su punto ciego:

- **El del e2e** (`ecg_synth._beat`, el de `tools/e2e_ml.py`): la misma forma a
  cualquier frecuencia. Mide solo la intrusión de los vecinos en la ventana.
- **Un modelo gaussiano con la repolarización adaptada**: la T se adelanta y se
  angosta con √RR (tipo Bazett), con histéresis opcional. Mide la T propia.
- **ECGSYN de NeuroKit** (`nk.ecg_simulate(method="ecgsyn")`), con el QRS
  compensado. ECGSYN escala también el ancho de Q, R y S con √(FC/60): a 155
  lpm su QRS sale un 40 % más angosto, cosa que el corazón no hace. Sin
  compensar, cualquier comparación de forma lo ve distinto —es otro QRS— y el
  test mediría el generador y no el motor.

Los registros pasan por el recorrido de producción (`_por_bloques`: 300 s con
60 s de contexto y 30 s de contexto derecho) y con el presupuesto de revisión
abierto: acá se mide qué marca el motor, no cuánto deja pasar el tope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pytest

from app.core.config import settings
from app.ml import morphology, pipeline
from app.ml.contracts import Finding
from app.ml.episodes import group_beats
from app.ml.morphology import (
    BeatMatrix,
    BeatRate,
    Template,
    TemplateBank,
    assign_and_update,
    bank_from_state,
    bank_to_state,
    beat_length,
    consolidate,
    count_anomalous,
    dissimilarity_to_dominant,
    extract_beats,
    is_recurrent,
    mark_reported,
    scoring_window,
    select_beats,
)
from tests.ecg_synth import SAMPLE_RATE
from tools.e2e_ml import Scenario, synth_scenario

BLOCK_S, CONTEXT_S, LOOKAHEAD_S = 300, 60, 30
#: Tolerancia para casar un R detectado con el de la verdad: 40 ms.
TOLERANCIA = int(0.040 * SAMPLE_RATE)


# --------------------------------------------------------------------------- #
# Generadores
# --------------------------------------------------------------------------- #

Perfil = Callable[[float], float]


def _perfil(puntos: list[tuple[float, float]]) -> Perfil:
    """Frecuencia (lpm) en cada segundo, interpolada entre `(segundo, lpm)`."""
    segundos, lpm = zip(*puntos, strict=True)
    return lambda t: float(np.interp(t, segundos, lpm))


@dataclass(frozen=True)
class Registro:
    señal: np.ndarray
    flags: np.ndarray
    #: R de todos los latidos y de los ectópicos, en muestras.
    latidos: np.ndarray
    ectopicos: np.ndarray
    #: Si cada ectópico cayó adentro de la taquicardia (> 100 lpm).
    en_taquicardia: np.ndarray


def _instantes(
    perfil: Perfil, duracion_s: float, *, ectopico_cada: int = 0, semilla: int = 3
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(R en segundos, si es ectópico, R-R de base de cada latido)`.

    El ectópico llega a 0,65 del R-R y nunca antes de 330 ms: más cerca cae
    sobre la T del anterior (R sobre T) y el detector de R no lo ve —NeuroKit no
    acepta dos R a menos de 300 ms—, que es otro problema y no el de acá. Lo
    sigue la pausa compensadora.
    """
    rng = np.random.default_rng(semilla)
    instantes, ectopicos, base = [0.6], [False], [60.0 / perfil(0.6)]
    indice = 1
    while instantes[-1] < duracion_s - 2.0:
        t = instantes[-1]
        rr = 60.0 / perfil(t) * (1.0 + 0.01 * rng.standard_normal())
        if ectopico_cada and indice % ectopico_cada == ectopico_cada - 1:
            instantes += [t + max(0.65 * rr, 0.33), t + 2.0 * rr]
            ectopicos += [True, False]
            base += [rr, rr]
            indice += 2
        else:
            instantes.append(t + rr)
            ectopicos.append(False)
            base.append(rr)
            indice += 1
    return np.array(instantes), np.array(ectopicos), np.array(base)


def _gaussiana(t: np.ndarray, centro: float, ancho: float, alto: float) -> np.ndarray:
    return np.asarray(alto * np.exp(-0.5 * ((t - centro) / ancho) ** 2))


def _latido_adaptado(t: np.ndarray, rr: float, *, ectopico: bool) -> np.ndarray:
    """Un latido cuya repolarización está adaptada a un R-R de `rr` segundos.

    La T se adelanta y se angosta con √RR (a 60 lpm, pico a 240 ms; a 120 lpm,
    a 170 ms) y la P se acerca un poco. El QRS no cambia. El ectópico es
    ventricular: sin P, QRS ancho y T opuesta.
    """
    raiz = np.sqrt(rr)
    if ectopico:
        return (
            _gaussiana(t, -0.03, 0.030, -0.20)
            + _gaussiana(t, 0.0, 0.034, 1.25)
            + _gaussiana(t, 0.07, 0.030, -0.35)
            + _gaussiana(t, 0.26 * raiz, 0.06 * raiz, -0.40)
        )
    return (
        _gaussiana(t, -0.16 * np.sqrt(raiz), 0.025, 0.12)
        + _gaussiana(t, -0.02, 0.010, -0.15)
        + _gaussiana(t, 0.0, 0.012, 1.0)
        + _gaussiana(t, 0.025, 0.012, -0.25)
        + _gaussiana(t, 0.24 * raiz, 0.05 * raiz, 0.32)
    )


def _qt_con_histeresis(instantes: np.ndarray, base: np.ndarray, tau_s: float) -> np.ndarray:
    """El R-R al que está adaptada la repolarización: la base con un retardo de `tau_s`.

    El QT tarda del orden de un minuto en alcanzar a la frecuencia: al terminar
    un esfuerzo, la T sigue siendo la de la frecuencia alta un rato.
    """
    if tau_s <= 0:
        return base.copy()
    adaptado = np.empty_like(base)
    actual = base[0]
    for indice, rr in enumerate(base):
        actual += (1.0 - np.exp(-rr / tau_s)) * (rr - actual)
        adaptado[indice] = actual
    return adaptado


def _registro(
    instantes: np.ndarray, ectopicos: np.ndarray, señal: np.ndarray, perfil: Perfil
) -> Registro:
    latidos = np.round(instantes * SAMPLE_RATE).astype(np.int64)
    return Registro(
        señal=señal.astype(np.float32),
        flags=np.zeros(señal.size, dtype=np.uint8),
        latidos=latidos,
        ectopicos=latidos[ectopicos],
        en_taquicardia=np.array([perfil(t) > 100.0 for t in instantes[ectopicos]], dtype=bool),
    )


def _modelo_adaptado(
    puntos: list[tuple[float, float]],
    duracion_s: float,
    *,
    ectopico_cada: int = 15,
    tau_qt_s: float = 0.0,
) -> Registro:
    perfil = _perfil(puntos)
    instantes, ectopicos, base = _instantes(perfil, duracion_s, ectopico_cada=ectopico_cada)
    adaptado = _qt_con_histeresis(instantes, base, tau_qt_s)
    n = int(duracion_s * SAMPLE_RATE)
    señal = np.random.default_rng(7).normal(0.0, 0.008, n)
    for instante, ectopico, rr in zip(instantes, ectopicos, adaptado, strict=True):
        centro = int(round(instante * SAMPLE_RATE))
        bajo, alto = max(centro - SAMPLE_RATE, 0), min(centro + SAMPLE_RATE, n)
        t = (np.arange(bajo, alto) - centro) / SAMPLE_RATE
        señal[bajo:alto] += _latido_adaptado(t, float(rr), ectopico=bool(ectopico))
    return _registro(instantes, ectopicos, señal, perfil)


def _arranque_en_bigeminismo(
    duplas_s: float, duracion_s: float, *, despues_cada: int | None = 15
) -> Registro:
    """75 lpm con el modelo adaptado. Hasta `duplas_s`, cada sinusal seguido de
    una dupla ventricular (N V V, el doble de ventriculares que de normales: el
    foco es la plantilla dominante); después, sinusal con un ventricular cada
    `despues_cada` latidos y su pausa compensadora (con None, ninguno)."""
    rng = np.random.default_rng(5)
    rr = 0.8
    instantes, ectopicos = [0.6], [False]
    indice = 1
    while instantes[-1] < duracion_s - 3.0:
        t = instantes[-1]
        if t < duplas_s:
            instantes += [t + 0.55, t + 1.10, t + 3 * rr]
            ectopicos += [True, True, False]
            continue
        paso = rr * (1.0 + 0.01 * rng.standard_normal())
        if despues_cada is not None and indice % despues_cada == despues_cada - 1:
            instantes += [t + 0.65 * paso, t + 2.0 * paso]
            ectopicos += [True, False]
            indice += 2
        else:
            instantes.append(t + paso)
            ectopicos.append(False)
            indice += 1
    n = int(duracion_s * SAMPLE_RATE)
    señal = np.random.default_rng(7).normal(0.0, 0.008, n)
    for instante, ectopico in zip(instantes, ectopicos, strict=True):
        centro = int(round(instante * SAMPLE_RATE))
        bajo, alto = max(centro - SAMPLE_RATE, 0), min(centro + SAMPLE_RATE, n)
        t = (np.arange(bajo, alto) - centro) / SAMPLE_RATE
        señal[bajo:alto] += _latido_adaptado(t, rr, ectopico=ectopico)
    return _registro(np.array(instantes), np.array(ectopicos, dtype=bool), señal, lambda _: 75.0)


def _del_e2e(minutos: float, taquicardia: tuple[float, float, float]) -> Registro:
    """El registro de `tools/e2e_ml.py` con sus valores por defecto: 60 lpm, un
    ventricular cada 12 latidos, 30 s de electrodo suelto a los 300 s y el tramo
    `(inicio_s, duración_s, lpm)` de taquicardia.

    Es el latido de `ecg_synth`, con la misma forma a cualquier frecuencia, y el
    ectópico llega al 60 % del R-R: adentro de la taquicardia es un R sobre T que
    el detector de R no ve.
    """
    escenario = Scenario(
        minutes=minutos,
        bpm=60.0,
        ectopic_every=12,
        tachy=(taquicardia,),
        pauses=(),
        lead_off=((300.0, 30.0),),
        lead_off_flat=False,
        noise_uv=8.0,
        seed=7,
    )
    señal, flags, verdad = synth_scenario(escenario)
    inicio, duracion, _ = taquicardia
    en_taquicardia = (verdad.ectopic >= inicio * SAMPLE_RATE) & (
        verdad.ectopic < (inicio + duracion) * SAMPLE_RATE
    )
    return Registro(
        señal=señal.astype(np.float32),
        flags=flags,
        latidos=verdad.rpeaks,
        ectopicos=verdad.ectopic,
        en_taquicardia=en_taquicardia,
    )


#: Ángulos (°), amplitudes y anchos de P, Q, R, S y T por defecto de ECGSYN.
_ECGSYN_TI = (-70.0, -15.0, 0.0, 15.0, 100.0)
_ECGSYN_AI = (1.2, -5.0, 30.0, -7.5, 0.75)
_ECGSYN_BI = (0.25, 0.1, 0.1, 0.1, 0.4)


def _ciclo_ecgsyn(lpm: int) -> tuple[np.ndarray, np.ndarray]:
    """8 s de ECGSYN a `lpm`, con Q, R y S en el tiempo de 60 lpm, y sus R.

    ECGSYN multiplica los anchos por √(FC/60) y los ángulos de Q y S por lo
    mismo; en el tiempo eso es un QRS ∝ RR^0,5. Se le pasan Q, R y S divididos
    por ese factor y por el R-R —y la amplitud por el R-R, que con el ancho
    también cambiaba—, así que en el tiempo quedan como a 60 lpm. P y T siguen
    la adaptación de ECGSYN: la T llega antes a más frecuencia.
    """
    import neurokit2 as nk

    rr = 60.0 / lpm
    factor = np.sqrt(lpm / 60.0)
    ti, ai, bi = list(_ECGSYN_TI), list(_ECGSYN_AI), list(_ECGSYN_BI)
    for onda in (1, 2, 3):
        ti[onda] = _ECGSYN_TI[onda] / (rr * factor)
        ai[onda] = _ECGSYN_AI[onda] * rr
        bi[onda] = _ECGSYN_BI[onda] / (rr * factor)
    señal = np.asarray(
        nk.ecg_simulate(
            duration=8,
            sampling_rate=SAMPLE_RATE,
            heart_rate=lpm,
            heart_rate_std=0.5,
            method="ecgsyn",
            random_state=1,
            noise=0.0,
            ti=tuple(ti),
            ai=tuple(ai),
            bi=tuple(bi),
        ),
        dtype=np.float64,
    )
    umbral = 0.6 * señal.max()
    candidatos = (
        np.flatnonzero(
            (señal[1:-1] > señal[:-2]) & (señal[1:-1] >= señal[2:]) & (señal[1:-1] > umbral)
        )
        + 1
    )
    picos: list[int] = []
    for candidato in candidatos.tolist():
        if picos and candidato - picos[-1] < 0.5 * rr * SAMPLE_RATE:
            if señal[candidato] > señal[picos[-1]]:
                picos[-1] = candidato
            continue
        picos.append(candidato)
    return señal, np.array(picos, dtype=np.int64)


def _ecgsyn(puntos: list[tuple[float, float]], duracion_s: float) -> Registro:
    """Un ECG de ECGSYN que sigue el perfil, ciclo por ciclo.

    Cada latido es un ciclo de una señal de ECGSYN a su frecuencia (redondeada a
    5 lpm), cortado en diástole: desde el 45 % del R-R anterior hasta el 55 %
    del suyo.
    """
    perfil = _perfil(puntos)
    instantes, _, base = _instantes(perfil, duracion_s)
    n = int(duracion_s * SAMPLE_RATE)
    señal = np.random.default_rng(7).normal(0.0, 0.008, n)
    ciclos: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for indice, (instante, rr) in enumerate(zip(instantes, base, strict=True)):
        lpm = int(5 * round(60.0 / rr / 5.0))
        if lpm not in ciclos:
            ciclos[lpm] = _ciclo_ecgsyn(lpm)
        ciclo, picos = ciclos[lpm]
        pico = int(picos[len(picos) // 2 + indice % 3 - 1])
        antes = int(round(0.45 * (base[indice - 1] if indice else rr) * SAMPLE_RATE))
        despues = int(round(0.55 * rr * SAMPLE_RATE))
        centro = int(round(instante * SAMPLE_RATE))
        bajo, alto = max(centro - antes, 0), min(centro + despues, n)
        señal[bajo:alto] += ciclo[pico - (centro - bajo) : pico + (alto - centro)]
    return _registro(instantes, np.zeros(instantes.size, dtype=bool), señal, perfil)


# --------------------------------------------------------------------------- #
# Recorrido por bloques
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Corrida:
    #: Episodios `morphology_anomaly`, en coordenadas del registro.
    episodios: list[Finding]
    #: Encabezados `recurrent_morphology` al final.
    recurrentes: list[Finding]
    banco: TemplateBank
    #: R de cada latido que algún bloque plegó, si pasó el umbral y su plantilla.
    latidos: np.ndarray
    marcados: np.ndarray
    plantillas: np.ndarray
    #: El banco después de cada bloque.
    bancos: list[TemplateBank]
    #: Por bloque, los encabezados que emitió y los clusters con los que
    #: `group_beats` deja pasar un latido suelto.
    encabezados: list[list[Finding]]
    agrupables: list[frozenset[int]]
    #: Por bloque, los encabezados que dio de baja (`retracted_headers`).
    retirados: list[tuple[int, ...]] = field(default_factory=list)

    def en_la_base(self) -> dict[int, int | None]:
        """Los encabezados como quedan con la persistencia de verdad, que solo
        hace upsert: `cluster_id → beatCount` de la última vez que se emitió,
        salvo los que un bloque posterior dio de baja (antes de sus upserts)."""
        base: dict[int, int | None] = {}
        for numero, bloque in enumerate(self.encabezados):
            for cluster in self.retirados[numero] if numero < len(self.retirados) else ():
                base.pop(cluster, None)
            for encabezado in bloque:
                assert encabezado.cluster_id is not None
                base[encabezado.cluster_id] = encabezado.beat_count
        return base


def _config() -> pipeline.PipelineConfig:
    config = pipeline.build_config(settings, SAMPLE_RATE)
    return replace(
        config,
        intervals=None,
        budget=replace(config.budget, max_per_study=100_000, max_per_kind=100_000),
    )


def _por_bloques(
    registro: Registro,
    monkeypatch: pytest.MonkeyPatch,
    *,
    al_cerrar: Callable[[int, TemplateBank], TemplateBank] | None = None,
) -> Corrida:
    """El recorrido de `processing.append_ml_analysis`, sin la base.

    Espía `group_beats` para saber qué latidos puntuaron: un ectópico suelto de
    un foco que todavía no llegó a 30 miembros puntúa pero no sale como
    episodio, y la sensibilidad se mide por latido. `al_cerrar(fin, banco)`
    reemplaza el banco que sigue después del bloque que termina en `fin`
    (en muestras): es lo que hace la persistencia al guardarlo y leerlo.
    """
    capturas: list[dict[str, Any]] = []
    original = pipeline.group_beats

    def espiar(*args: Any, **kwargs: Any) -> list[Finding]:
        rpeaks, positive, cluster_ids, recurrent = args[0], args[1], args[3], args[4]
        owned = kwargs.get("owned")
        capturas.append(
            {
                "rpeaks": np.asarray(rpeaks).copy(),
                "positive": np.asarray(positive).copy(),
                "cluster_ids": np.asarray(cluster_ids).copy(),
                "owned": np.ones(len(rpeaks), dtype=bool) if owned is None else owned.copy(),
                "recurrent": frozenset(recurrent),
            }
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "group_beats", espiar)
    config = _config()
    bloque, contexto, derecho = (s * SAMPLE_RATE for s in (BLOCK_S, CONTEXT_S, LOOKAHEAD_S))
    banco = pipeline.empty_bank(config)
    n = registro.señal.size
    episodios: list[Finding] = []
    recurrentes: list[Finding] = []
    latidos: list[int] = []
    marcados: list[bool] = []
    plantillas: list[int] = []
    bancos: list[TemplateBank] = []
    encabezados: list[list[Finding]] = []
    agrupables: list[frozenset[int]] = []
    retirados: list[tuple[int, ...]] = []
    inicio = 0
    while inicio < n:
        fin = min(inicio + bloque, n)
        lectura, hasta = max(0, inicio - contexto), min(fin + derecho, n)
        capturas.clear()
        resultado = pipeline.analyze_batch(
            registro.señal[lectura:hasta],
            registro.flags[lectura:hasta],
            start_sample_index=lectura,
            bank=banco,
            config=config,
            fold_key=f"{inicio}:{fin}",
            context_samples=inicio - lectura,
            lookahead_samples=hasta - fin,
        )
        banco = resultado.bank
        bancos.append(banco)
        episodios += [f for f in resultado.findings if f.kind == "morphology_anomaly"]
        recurrentes = [f for f in resultado.findings if f.kind == "recurrent_morphology"]
        encabezados.append(recurrentes)
        retirados.append(resultado.retracted_headers)
        agrupables.append(frozenset().union(*(c["recurrent"] for c in capturas)))
        if al_cerrar is not None:
            banco = al_cerrar(fin, banco)
        for captura in capturas:
            propios = captura["owned"]
            latidos += (captura["rpeaks"][propios] + lectura).tolist()
            marcados += captura["positive"][propios].tolist()
            plantillas += captura["cluster_ids"][propios].tolist()
        inicio = fin
    return Corrida(
        episodios=episodios,
        recurrentes=recurrentes,
        banco=banco,
        latidos=np.array(latidos, dtype=np.int64),
        marcados=np.array(marcados, dtype=bool),
        plantillas=np.array(plantillas, dtype=np.int64),
        bancos=bancos,
        encabezados=encabezados,
        agrupables=agrupables,
        retirados=retirados,
    )


def _cerca(muestras: np.ndarray, referencia: np.ndarray) -> np.ndarray:
    """Por muestra: si cae a menos de `TOLERANCIA` de alguna de `referencia`."""
    if referencia.size == 0 or muestras.size == 0:
        return np.zeros(muestras.size, dtype=bool)
    orden = np.sort(referencia)
    derecha = np.clip(np.searchsorted(orden, muestras), 0, orden.size - 1)
    izquierda = np.clip(derecha - 1, 0, orden.size - 1)
    distancia = np.minimum(np.abs(muestras - orden[derecha]), np.abs(muestras - orden[izquierda]))
    return np.asarray(distancia <= TOLERANCIA)


def _falsos(corrida: Corrida, registro: Registro) -> list[Finding]:
    """Episodios sin ningún ectópico verdadero adentro."""
    return [
        episodio
        for episodio in corrida.episodios
        if not _cerca(np.array(episodio.beat_samples, dtype=np.int64), registro.ectopicos).any()
    ]


def _normales_marcados(corrida: Corrida, registro: Registro) -> int:
    return int(np.count_nonzero(corrida.marcados & ~_cerca(corrida.latidos, registro.ectopicos)))


def _ectopicos_marcados(corrida: Corrida, registro: Registro) -> np.ndarray:
    """Por ectópico verdadero: si el detector lo vio y puntuó."""
    vistos = corrida.latidos[corrida.marcados]
    return _cerca(registro.ectopicos, vistos)


def _solo_focos_ectopicos(corrida: Corrida, registro: Registro) -> None:
    """Los encabezados recurrentes son todos del foco ventricular, y lo cubren.

    Puede ser más de uno: en el modelo adaptado la T del ectópico también sigue
    a la frecuencia, y el de 150 lpm abre su propia plantilla. Lo que no puede
    haber es uno hecho de latidos normales.
    """
    assert corrida.recurrentes, "el foco ventricular no se informó"
    for foco in corrida.recurrentes:
        miembros = corrida.latidos[corrida.plantillas == foco.cluster_id]
        assert _cerca(miembros, registro.ectopicos).mean() >= 0.9, (
            foco.cluster_id,
            foco.beat_count,
            foco.metadata,
        )
    # Los ectópicos de adentro de la taquicardia pueden quedar en una plantilla
    # propia que no llega a 30: son latidos marcados igual, pero no un foco.
    total = sum(foco.beat_count or 0 for foco in corrida.recurrentes)
    assert 0.8 * registro.ectopicos.size <= total <= 1.05 * registro.ectopicos.size


# --------------------------------------------------------------------------- #
# La ventana del score
# --------------------------------------------------------------------------- #


def test_la_ventana_del_score_es_la_entera_hasta_109_lpm_y_despues_se_recorta() -> None:
    """Hasta un R-R esperado de 550 ms el score es exactamente el de siempre.
    Más rápido deja afuera los primeros 300 ms después del R anterior —su QRS,
    su ST y el grueso de su T— y los 200 ms antes del siguiente —su P—, sin
    bajar nunca de 60 ms antes del R ni de 120 ms después: el QRS entero."""
    largo = beat_length(SAMPLE_RATE)
    centro = largo // 2
    esperado = np.array([np.nan, 1.0, 0.55, 0.40, 0.25], dtype=np.float32)
    ventana = scoring_window(esperado, SAMPLE_RATE)

    assert ventana.start.tolist()[:3] == [0, 0, 0]
    assert ventana.stop.tolist()[:3] == [largo, largo, largo]
    # 150 lpm: de −100 ms a +200 ms.
    assert ventana.start[3] == centro - 50
    assert ventana.stop[3] == centro + 100
    # 240 lpm: los mínimos.
    assert ventana.start[4] == centro - 30
    assert ventana.stop[4] == centro + 60


# --------------------------------------------------------------------------- #
# Un latido contra la dominante
# --------------------------------------------------------------------------- #


def _ventanas(
    rr: float,
    *,
    ectopico: bool = False,
    previo: float | None = None,
    repolarizacion: float | None = None,
) -> BeatMatrix:
    """La ventana del tercero de cinco latidos a `rr`, con el modelo adaptado.

    `previo` es el R-R que lo trae, si llegó antes de tiempo. `ectopico` lo
    hace ventricular. `repolarizacion` es el R-R al que está adaptada la T, si
    no es `rr` (la histéresis del QT).
    """
    instantes = [0.6, 0.6 + rr]
    instantes.append(instantes[-1] + (previo if previo is not None else rr))
    instantes += [instantes[1] + 2 * rr, instantes[1] + 3 * rr]
    duracion = instantes[-1] + 1.0
    n = int(duracion * SAMPLE_RATE)
    señal = np.zeros(n)
    for indice, instante in enumerate(instantes):
        centro = int(round(instante * SAMPLE_RATE))
        bajo, alto = max(centro - SAMPLE_RATE, 0), min(centro + SAMPLE_RATE, n)
        t = (np.arange(bajo, alto) - centro) / SAMPLE_RATE
        señal[bajo:alto] += _latido_adaptado(
            t, repolarizacion or rr, ectopico=ectopico and indice == 2
        )
    picos = np.round(np.array(instantes) * SAMPLE_RATE).astype(np.int64)
    latidos = extract_beats(señal.astype(np.float32), picos, np.ones(n, dtype=bool), SAMPLE_RATE)
    return select_beats(latidos, latidos.beat_index == 2)


def _banco_en_reposo(*, con_frecuencia: bool = True) -> TemplateBank:
    """Un banco cuya dominante se aprendió con 80 latidos a 60 lpm."""
    instantes = 0.6 + np.arange(80, dtype=np.float64)
    n = int((instantes[-1] + 1.0) * SAMPLE_RATE)
    señal = np.zeros(n)
    for instante in instantes:
        centro = int(round(instante * SAMPLE_RATE))
        bajo, alto = max(centro - SAMPLE_RATE, 0), min(centro + SAMPLE_RATE, n)
        señal[bajo:alto] += _latido_adaptado(
            (np.arange(bajo, alto) - centro) / SAMPLE_RATE, 1.0, ectopico=False
        )
    picos = np.round(instantes * SAMPLE_RATE).astype(np.int64)
    latidos = extract_beats(señal.astype(np.float32), picos, np.ones(n, dtype=bool), SAMPLE_RATE)
    banco, _ = assign_and_update(
        TemplateBank(model_version="test-1", beat_length=beat_length(SAMPLE_RATE)),
        latidos,
        match_threshold=0.90,
        max_templates=40,
        expected_rr=np.full(latidos.n_beats, 1.0, dtype=np.float32) if con_frecuencia else None,
    )
    return banco


def _ritmo(rr: float, prematuridad: float = 1.0) -> BeatRate:
    return BeatRate(
        expected_rr=np.array([rr], dtype=np.float32),
        prematurity=np.array([prematuridad], dtype=np.float32),
        sample_rate=SAMPLE_RATE,
    )


@pytest.mark.parametrize(("lpm", "disimilitud_sin_ritmo"), [(120, 0.10), (150, 0.20)])
def test_un_latido_sinusal_rapido_se_parece_a_la_dominante_a_su_frecuencia(
    lpm: int, disimilitud_sin_ritmo: float
) -> None:
    """A 150 lpm, contra la ventana entera, un latido normal queda a 0,22 de la
    dominante de reposo: sin ser prematuro puntúa 0,5 y es un hallazgo. Mirado a
    su frecuencia —sin los vecinos y con la T de la dominante adelantada como
    se adelanta el QT— es la misma forma."""
    banco = _banco_en_reposo()
    latido = _ventanas(60.0 / lpm)

    assert dissimilarity_to_dominant(banco, latido)[0] > disimilitud_sin_ritmo
    assert dissimilarity_to_dominant(banco, latido, _ritmo(60.0 / lpm))[0] < 0.03


def test_un_ectopico_ventricular_rapido_sigue_siendo_otra_forma() -> None:
    """La adaptación toca solo la repolarización, nunca el QRS: un ventricular
    que llega a tiempo dentro de una taquicardia sigue lejísimos de la
    dominante, y puntúa lo mismo que sin ella."""
    banco = _banco_en_reposo()
    ectopico = _ventanas(0.5, ectopico=True)

    disimilitud = dissimilarity_to_dominant(banco, ectopico, _ritmo(0.5))[0]
    assert disimilitud > 2 * (1.0 - 0.90)  # el score de forma satura en 1
    score = morphology.anomaly_score(np.array([disimilitud]), np.array([1.0]), match_threshold=0.90)
    assert score[0] >= settings.ml_anomaly_score_min


def test_un_prematuro_se_compara_sin_adaptar_la_repolarizacion() -> None:
    """Un latido que llegó antes de tiempo no se mira "a su frecuencia": su
    ritmo no cambió, él se adelantó. Con prematuridad 0,6 dentro de una
    taquicardia, su disimilitud es la de la ventana recortada sola, la misma
    que contra una dominante cuya frecuencia no se conoce."""
    latido = _ventanas(0.5, previo=0.3)
    prematuro = _ritmo(0.5, prematuridad=0.6)

    sin_adaptar = dissimilarity_to_dominant(
        _banco_en_reposo(con_frecuencia=False), latido, prematuro
    )
    con_banco = dissimilarity_to_dominant(_banco_en_reposo(), latido, prematuro)
    a_tiempo = dissimilarity_to_dominant(_banco_en_reposo(), latido, _ritmo(0.5))

    assert con_banco[0] == pytest.approx(sin_adaptar[0])
    assert a_tiempo[0] <= con_banco[0]


def test_en_reposo_el_score_no_cambia() -> None:
    """A la frecuencia a la que se aprendió la dominante —y hasta un paso de la
    grilla de distancia— la comparación es el producto punto de siempre: lo
    que se midió sobre MIT-BIH en reposo sigue valiendo."""
    banco = _banco_en_reposo()
    for rr in (1.0, 0.95):
        latido = _ventanas(rr)
        assert dissimilarity_to_dominant(banco, latido, _ritmo(rr))[0] == pytest.approx(
            dissimilarity_to_dominant(banco, latido)[0], abs=1e-6
        )


def test_la_t_de_la_recuperacion_se_mira_con_la_frecuencia_del_ultimo_minuto() -> None:
    """Al bajar de una escalera el latido ya llega a 60 lpm, pero su T sigue
    siendo la de 120: el QT tarda un minuto en alcanzar a la frecuencia. Con un
    latido rápido 30 s antes, la dominante se compara también comprimida hasta
    esa frecuencia; 90 s después, ya no."""
    banco = _banco_en_reposo()
    rapido = _ventanas(0.5)
    recuperacion = _ventanas(1.0, repolarizacion=0.5)

    def contra(separacion_s: float) -> float:
        latidos = BeatMatrix(
            beat_index=np.array([0, 1], dtype=np.int64),
            rpeaks=np.array([0, int(separacion_s * SAMPLE_RATE)], dtype=np.int64),
            waveforms=np.vstack((rapido.waveforms, recuperacion.waveforms)),
        )
        ritmo = BeatRate(
            expected_rr=np.array([0.5, 1.0], dtype=np.float32),
            prematurity=np.ones(2, dtype=np.float32),
            sample_rate=SAMPLE_RATE,
        )
        return float(dissimilarity_to_dominant(banco, latidos, ritmo)[1])

    sola = float(dissimilarity_to_dominant(banco, recuperacion, _ritmo(1.0))[0])
    assert sola > 0.05
    assert contra(30.0) < 0.02
    assert contra(90.0) == pytest.approx(sola)


# --------------------------------------------------------------------------- #
# Foco recurrente
# --------------------------------------------------------------------------- #


def _plantilla(
    cluster_id: int,
    count: int,
    anomalous: int,
    *,
    scored: int | None = None,
    reported: bool | None = False,
) -> Template:
    """Una plantilla de prueba. Sin `scored`, todos sus miembros pudieron
    puntuar: nunca fue la dominante."""
    centroide = np.zeros(beat_length(SAMPLE_RATE), dtype=np.float32)
    centroide[cluster_id] = 1.0
    return Template(
        cluster_id=cluster_id,
        centroid=centroide,
        count=count,
        sum_correlation=0.98 * count,
        first_sample=0,
        last_sample=1_000,
        scored_count=count if scored is None else scored,
        anomalous_count=anomalous,
        reported=reported,
    )


def _banco(*plantillas: Template) -> TemplateBank:
    return TemplateBank(
        model_version="test-1",
        beat_length=beat_length(SAMPLE_RATE),
        templates=plantillas,
        beats_seen=sum(t.count for t in plantillas),
    )


def _marcar(banco: TemplateBank) -> TemplateBank:
    config = _config()
    return mark_reported(
        banco,
        min_beats=config.recurrent_min_beats,
        min_anomalous_fraction=config.recurrent_min_anomalous_fraction,
    )


def test_una_variante_de_la_forma_normal_no_es_un_foco() -> None:
    """Una plantilla con 118 latidos de los que puntuaron 3 es la que abría la
    taquicardia del e2e: no es un foco. Una con 93 de 93 sí."""
    taquicardia = _plantilla(1, 118, 3)
    foco = _plantilla(2, 93, 93)

    assert not is_recurrent(taquicardia, min_beats=30, min_anomalous_fraction=0.2)
    assert is_recurrent(foco, min_beats=30, min_anomalous_fraction=0.2)
    # Sin la condición, como antes: el conteo solo.
    assert is_recurrent(taquicardia, min_beats=30, min_anomalous_fraction=0.0)
    assert not is_recurrent(_plantilla(3, 29, 29), min_beats=30, min_anomalous_fraction=0.2)


def test_un_supraventricular_suelto_en_una_variante_normal_se_sigue_informando() -> None:
    """El 213 de MIT-BIH: sus supraventriculares caen en el cluster 0 —464
    normales, 20 supraventriculares y 98 de fusión, 11 de 583 puntuaron—, que
    no es la dominante. Un supraventricular tiene el QRS normal y cae en
    cualquier variante de la forma normal. La variante no es un foco y no tiene
    encabezado, pero el latido suyo que puntuó sale como episodio aunque esté
    solo: con la fracción también para agrupar, los 10 que se informaban
    pasaban a 0."""
    variante = _plantilla(0, 583, 11)
    dominante = _plantilla(1, 1_995, 0, scored=0)
    banco = _marcar(_banco(variante, dominante))
    config = _config()

    assert not banco.templates[0].reported
    assert pipeline._recurrent_findings(banco, config) == []
    agrupables = banco.recurrent_ids(config.recurrent_min_beats)
    assert agrupables == frozenset({0, 1})

    latidos = 10
    positivo = np.zeros(latidos, dtype=bool)
    positivo[5] = True
    clusters = np.ones(latidos, dtype=np.int64)
    clusters[5] = 0
    (episodio,) = group_beats(
        (np.arange(latidos) + 1) * SAMPLE_RATE,
        positivo,
        np.where(positivo, 0.42, 0.02),
        clusters,
        agrupables,
        sample_rate=SAMPLE_RATE,
        budget=config.budget,
    )
    assert (episodio.cluster_id, episodio.beat_count) == (0, 1)


def test_los_miembros_de_la_dominante_no_cuentan_como_puntuables() -> None:
    """El score se mide contra la dominante: sus miembros dan ~0 por
    construcción y no dicen nada de si es un foco. Se suman solo los de las
    demás, a los dos contadores."""
    banco = _banco(_plantilla(0, 10, 1), _plantilla(4, 5, 0), _plantilla(7, 50, 0, scored=0))
    sumado = count_anomalous(
        banco,
        np.array([0, 4, 4, -1, 0, 7, 7], dtype=np.int64),
        np.array([True, True, False, True, False, True, False]),
    )
    assert [(t.scored_count, t.anomalous_count) for t in sumado.templates] == [
        (12, 2),
        (7, 1),
        (0, 0),
    ]
    assert count_anomalous(banco, np.array([7, -1], dtype=np.int64), np.array([True, True])) is (
        banco
    )


def test_un_foco_que_arranco_dominante_califica_con_lo_que_puntuo_despues() -> None:
    """Un bigeminismo con duplas al principio del estudio: el foco ventricular
    fue la dominante y juntó 468 miembros que no podían puntuar. Cuando la
    forma normal recupera el lugar, el foco se juzga por los que llegaron
    después, que puntúan todos: con 30, ya es un foco. Antes se diluía en sus
    468 y quedaba sin informar decenas de minutos."""
    normal = _plantilla(0, 724, 375, scored=375, reported=True)
    foco = _plantilla(1, 493, 25, scored=25)
    assert not _marcar(_banco(normal, foco)).templates[1].reported

    foco = _plantilla(1, 518, 50, scored=50)
    marcado = _marcar(_banco(_plantilla(0, 1_074, 375, scored=375, reported=True), foco))
    assert marcado.templates[1].reported
    (encabezado,) = pipeline._recurrent_findings(marcado, _config())
    assert (encabezado.cluster_id, encabezado.beat_count) == (1, 518)


def test_un_foco_informado_se_sigue_informando_con_el_conteo_al_dia() -> None:
    """La fracción baja cuando la plantilla suma miembros que no puntúan, y la
    persistencia solo hace upsert de lo que se emite: si el encabezado dejara de
    emitirse, quedaría en la base con el conteo de cuando calificó. Una vez
    informado, se sigue informando."""
    dominante = _plantilla(0, 1_000, 0, scored=0)
    marcado = _marcar(_banco(dominante, _plantilla(1, 40, 10)))
    assert marcado.templates[1].reported

    diluido = replace(marcado.templates[1], count=300, scored_count=300)
    assert not is_recurrent(diluido, min_beats=30, min_anomalous_fraction=0.2)
    banco = _marcar(_banco(dominante, diluido))
    (encabezado,) = pipeline._recurrent_findings(banco, _config())
    assert encabezado.beat_count == 300
    # Determinista: volver a marcar no cambia nada.
    assert _marcar(banco) is banco
    # La dominante nunca se marca, aunque su fracción alcance.
    assert not _marcar(_banco(_plantilla(0, 1_000, 900))).templates[0].reported


def test_un_banco_viejo_conserva_los_encabezados_que_ya_tenia_escritos() -> None:
    """Un banco de antes de los contadores trae la marca sin resolver. Se
    resuelve con la regla de entonces —no dominante con 30 miembros—, que es el
    encabezado que ese estudio ya tiene escrito en la base: se sigue
    actualizando. Las demás se juzgan por lo que se pliegue de ahora en más."""
    banco = _marcar(
        _banco(
            _plantilla(0, 1_000, 0, scored=0, reported=None),
            _plantilla(1, 118, 0, scored=0, reported=None),
            _plantilla(2, 20, 0, scored=0, reported=None),
        )
    )
    assert [t.reported for t in banco.templates] == [False, True, False]
    (encabezado,) = pipeline._recurrent_findings(banco, _config())
    assert encabezado.cluster_id == 1


def test_el_estado_guarda_los_contadores_nuevos() -> None:
    """Los contadores, la marca y la frecuencia viajan en el estado. Un banco de
    antes no los trae: arranca en cero, con la marca sin resolver y la frecuencia
    desconocida. Uno con `anomalousCount` pero sin `scoredCount` también es
    viejo: ese contador incluía a la dominante."""
    plantilla = replace(
        _plantilla(3, 40, 12, scored=35, reported=True), expected_rr_sum=24.0, expected_rr_beats=40
    )
    banco = TemplateBank(
        model_version="m", beat_length=beat_length(SAMPLE_RATE), templates=(plantilla,)
    )
    estado, blob = bank_to_state(banco)
    (vuelta,) = bank_from_state(estado, blob, model_version="m").templates
    assert (vuelta.scored_count, vuelta.anomalous_count, vuelta.reported) == (35, 12, True)
    assert vuelta.mean_expected_rr == pytest.approx(0.6)

    for item in estado["templates"]:
        del item["scoredCount"], item["reported"], item["expectedRrSum"], item["expectedRrBeats"]
    (intermedio,) = bank_from_state(estado, blob, model_version="m").templates
    for item in estado["templates"]:
        del item["anomalousCount"]
    (viejo,) = bank_from_state(estado, blob, model_version="m").templates
    for plantilla_vieja in (intermedio, viejo):
        assert (plantilla_vieja.count, plantilla_vieja.scored_count) == (40, 0)
        assert (plantilla_vieja.anomalous_count, plantilla_vieja.reported) == (0, None)
        assert plantilla_vieja.mean_expected_rr is None


def test_fundir_plantillas_suma_los_contadores() -> None:
    a = replace(_plantilla(0, 30, 3), expected_rr_sum=30.0, expected_rr_beats=30)
    b = replace(
        a,
        cluster_id=5,
        count=10,
        scored_count=8,
        anomalous_count=1,
        expected_rr_sum=5.0,
        expected_rr_beats=10,
        reported=True,
    )
    banco = TemplateBank(model_version="m", beat_length=a.centroid.size, templates=(a, b))
    fundido, mapa = consolidate(banco, merge_threshold=0.95)
    (unica,) = fundido.templates
    assert mapa == {5: 0}
    assert (unica.count, unica.scored_count, unica.anomalous_count) == (40, 38, 4)
    assert unica.reported is True
    assert unica.mean_expected_rr == pytest.approx(35.0 / 40)

    viejo = replace(b, reported=None)
    (sin_resolver,) = consolidate(replace(banco, templates=(a, viejo)), merge_threshold=0.95)[
        0
    ].templates
    assert sin_resolver.reported is None


# --------------------------------------------------------------------------- #
# Registros enteros
# --------------------------------------------------------------------------- #

#: 60 lpm, sube a 150 en 90 s, se queda 60 s, baja a 60 en 2 min. Diez minutos.
RAMPA = [(0.0, 60.0), (150.0, 60.0), (240.0, 150.0), (300.0, 150.0), (420.0, 60.0), (600.0, 60.0)]


def test_el_defecto_del_e2e_taquicardia_brusca_a_155_lpm(monkeypatch: pytest.MonkeyPatch) -> None:
    """El registro del e2e en vivo: 20 min a 60 lpm con un foco ventricular cada
    12 latidos y 60 s a 155 lpm desde el minuto 10. Antes: un segundo foco con
    118 latidos normales de la taquicardia (9,4 % de carga) y 13 episodios de 9
    latidos encima de ella (14 en el estudio en vivo).

    Quedan los dos bordes, que con un salto de un latido al siguiente la
    mediana de la prematuridad —centrada— todavía no alcanzó: ≤ 2 latidos en el
    arranque, que llegan "antes de tiempo", y el último de la taquicardia, que
    contra una mediana que ya mira el reposo también parece prematuro. Así
    arranca y termina una supraventricular, no una taquicardia sinusal —que
    acelera y frena en segundos—, y marcarlo es lo que tiene que pasar. El del
    final sale como episodio aunque esté solo: cae en la plantilla de la
    taquicardia, que no es un foco pero tiene 30 miembros, y con eso un latido
    suelto que puntuó se informa (es como se ven los supraventriculares, ver
    `test_un_supraventricular_suelto_en_una_variante_normal_se_sigue_informando`)."""
    registro = _del_e2e(20.0, (600.0, 60.0, 155.0))
    corrida = _por_bloques(registro, monkeypatch)

    _solo_focos_ectopicos(corrida, registro)
    adentro = (corrida.latidos >= 600 * SAMPLE_RATE) & (corrida.latidos < 660 * SAMPLE_RATE)
    assert adentro.sum() > 140  # la taquicardia se analizó
    normales = corrida.marcados & ~_cerca(corrida.latidos, registro.ectopicos)
    arranque = (corrida.latidos >= 600 * SAMPLE_RATE) & (corrida.latidos < 602 * SAMPLE_RATE)
    final = (corrida.latidos >= 659 * SAMPLE_RATE) & (corrida.latidos < 661 * SAMPLE_RATE)
    assert np.count_nonzero(normales & arranque) <= 2
    assert np.count_nonzero(normales & final) <= 1
    assert not np.any(normales & adentro & ~arranque & ~final)
    falsos = _falsos(corrida, registro)
    assert len(falsos) <= 2, falsos
    for episodio in falsos:
        segundos = [r / SAMPLE_RATE for r in episodio.beat_samples]
        assert all(600.0 <= r < 602.0 for r in segundos) or (
            len(segundos) == 1 and 659.0 <= segundos[0] < 661.0
        ), segundos

    # La plantilla de la taquicardia no tiene encabezado de foco, pero agrupa
    # como cualquiera con 30 miembros.
    clusters, cuantos = np.unique(corrida.plantillas[adentro], return_counts=True)
    taquicardia = int(clusters[np.argmax(cuantos)])
    assert cuantos.max() >= 100
    assert taquicardia not in corrida.en_la_base()
    assert taquicardia in corrida.agrupables[-1]

    # Los ventriculares de afuera de la taquicardia —los de adentro son R sobre
    # T y el detector no los ve— puntúan como antes (93 de 95).
    marcados = _ectopicos_marcados(corrida, registro)
    vistos = _cerca(registro.ectopicos, corrida.latidos)
    assert marcados[vistos & ~registro.en_taquicardia].mean() >= 0.97


def test_rampa_de_esfuerzo_con_el_qt_adaptado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una escalera: la frecuencia sube a 150 en un minuto y medio y vuelve. Con
    la T adelantándose como el QT, ni un latido normal marcado, ni un episodio
    falso, ni un foco que no sea el ventricular, que se sigue viendo entero
    adentro y afuera del esfuerzo."""
    registro = _modelo_adaptado(RAMPA, 600.0)
    corrida = _por_bloques(registro, monkeypatch)

    assert _falsos(corrida, registro) == []
    assert _normales_marcados(corrida, registro) == 0
    _solo_focos_ectopicos(corrida, registro)
    marcados = _ectopicos_marcados(corrida, registro)
    assert registro.en_taquicardia.sum() >= 10
    assert marcados.all()


def test_la_histeresis_del_qt_no_marca_la_recuperacion(monkeypatch: pytest.MonkeyPatch) -> None:
    """El QT tarda en alcanzar a la frecuencia: al bajar, la T sigue llegando
    temprano un minuto. Con un retardo de 60 s, la recuperación no se marca."""
    registro = _modelo_adaptado(RAMPA, 600.0, tau_qt_s=60.0)
    corrida = _por_bloques(registro, monkeypatch)

    assert _falsos(corrida, registro) == []
    _solo_focos_ectopicos(corrida, registro)
    assert _ectopicos_marcados(corrida, registro).all()


def test_escalones_bruscos_solo_marcan_el_arranque(monkeypatch: pytest.MonkeyPatch) -> None:
    """De 60 a 150 lpm y de 60 a 120 de un latido al siguiente, un minuto cada
    uno. Lo sostenido no se marca; a lo sumo los dos primeros latidos de cada
    arranque (ver `test_el_defecto_del_e2e_taquicardia_brusca_a_155_lpm`)."""
    arranques = (300.0, 600.0)
    registro = _modelo_adaptado(
        [(0, 60), (299.99, 60), (300, 150), (360, 150), (360.01, 60), (599.99, 60), (600, 120)]
        + [(660, 120), (660.01, 60), (900, 60)],
        900.0,
    )
    corrida = _por_bloques(registro, monkeypatch)

    normales = corrida.latidos[corrida.marcados & ~_cerca(corrida.latidos, registro.ectopicos)]
    for latido in normales.tolist():
        assert any(0 <= latido / SAMPLE_RATE - inicio < 2.0 for inicio in arranques), latido
    assert len(normales) <= 2 * len(arranques)
    _solo_focos_ectopicos(corrida, registro)
    assert _ectopicos_marcados(corrida, registro).all()


def test_la_dominante_aprendida_en_esfuerzo_no_marca_el_reposo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el estudio arranca caminando, la dominante se aprende a 110 lpm. El
    reposo que viene después es más lento que ella: la repolarización de la
    dominante se estira en vez de comprimirse, y el reposo no se marca."""
    registro = _modelo_adaptado([(0, 110), (420, 110), (480, 60), (900, 60)], 900.0)
    corrida = _por_bloques(registro, monkeypatch)

    assert _falsos(corrida, registro) == []
    assert _normales_marcados(corrida, registro) == 0
    _solo_focos_ectopicos(corrida, registro)


@pytest.mark.parametrize(
    "puntos",
    [
        pytest.param(RAMPA, id="rampa"),
        pytest.param([(0, 60), (240, 60), (300, 120), (360, 120), (420, 170)], id="hasta-170"),
    ],
)
def test_ecgsyn_con_qrs_fisiologico(
    puntos: list[tuple[float, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """El modelo dinámico de ECGSYN, con la T que se adelanta como la de ECGSYN
    —más que Bazett— y el QRS compensado (ver el docstring del módulo). Sin
    ectópicos: ni un episodio ni un foco."""
    registro = _ecgsyn(puntos, max(t for t, _ in puntos) + 60.0)
    corrida = _por_bloques(registro, monkeypatch)

    assert corrida.episodios == []
    assert corrida.recurrentes == []
    assert _normales_marcados(corrida, registro) == 0


def test_la_dominante_aprendida_a_185_lpm_no_marca_la_recuperacion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un estudio que arranca con 8 minutos a 185 lpm: la dominante se aprende
    ahí, con la T del latido anterior y la P del siguiente adentro de su
    centroide. El reposo que sigue se compara sin esos vecinos —la ventana sale
    del R-R de la dominante—. Antes: 708 latidos de reposo marcados, 20
    episodios y dos focos hechos de latidos normales."""
    registro = _modelo_adaptado(
        [(0, 185), (480, 185), (600, 75), (1200, 75)], 1200.0, ectopico_cada=0
    )
    corrida = _por_bloques(registro, monkeypatch)

    dominante = morphology.dominant_template(corrida.banco)
    assert dominante is not None and dominante.mean_expected_rr is not None
    assert dominante.mean_expected_rr < 0.4  # la dominante es la del esfuerzo
    assert _normales_marcados(corrida, registro) == 0
    assert corrida.episodios == []
    assert corrida.en_la_base() == {}


def test_un_foco_que_arranco_dominante_vuelve_a_informarse_enseguida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Los primeros 5 minutos en bigeminismo con duplas: el foco ventricular es
    la dominante y junta sus miembros sin poder puntuar —se puntúan contra
    ella—. Cuando la forma normal recupera el lugar, cada ventricular que llega
    se informa, y el encabezado del foco vuelve en cuanto 30 puntuaron. Antes
    sus 250 miembros del arranque lo diluían: bloques enteros sin informar
    ningún ventricular y sin encabezado."""
    duplas_s = 300.0
    registro = _arranque_en_bigeminismo(duplas_s, 1200.0)
    corrida = _por_bloques(registro, monkeypatch)

    primero = morphology.dominant_template(corrida.bancos[0])
    assert primero is not None
    miembros = corrida.latidos[corrida.plantillas == primero.cluster_id]
    assert _cerca(miembros, registro.ectopicos).mean() >= 0.9  # arrancó dominante

    # Desde que la normal es la dominante, ningún ventricular queda afuera.
    despues = registro.ectopicos[registro.ectopicos >= (duplas_s + 10) * SAMPLE_RATE]
    en_episodios = np.array(
        [s for episodio in corrida.episodios for s in episodio.beat_samples], dtype=np.int64
    )
    assert despues.size >= 60
    assert _cerca(despues, en_episodios).all()

    # Y el encabezado del foco está desde el tercer bloque, con todos sus miembros.
    for bloque in corrida.encabezados[2:]:
        assert primero.cluster_id in {f.cluster_id for f in bloque}
    assert corrida.en_la_base()[primero.cluster_id] == pytest.approx(
        registro.ectopicos.size, rel=0.05
    )


def _supraventriculares_en_taquicardia(lpm: float) -> Registro:
    """70 lpm, diez minutos a `lpm` desde los 300 s y vuelta a 70. En la
    meseta, cada 6 latidos uno supraventricular: el QRS y la T normales, sin la
    P sinusal, a 0,75 del R-R."""
    rng = np.random.default_rng(11)
    instantes, ectopicos, rrs = [0.6], [False], [60.0 / 70.0]
    indice = 1
    while instantes[-1] < 1197.0:
        t = instantes[-1]
        rr = (60.0 / lpm if 300.0 <= t < 900.0 else 60.0 / 70.0) * (
            1.0 + 0.01 * rng.standard_normal()
        )
        prematuro = 310.0 <= t < 890.0 and indice % 6 == 5
        instantes.append(t + (0.75 if prematuro else 1.0) * rr)
        ectopicos.append(prematuro)
        rrs.append(rr)
        indice += 1
    n = int(1200.0 * SAMPLE_RATE)
    señal = np.random.default_rng(7).normal(0.0, 0.008, n)
    for instante, ectopico, rr in zip(instantes, ectopicos, rrs, strict=True):
        centro = int(round(instante * SAMPLE_RATE))
        bajo, alto = max(centro - SAMPLE_RATE, 0), min(centro + SAMPLE_RATE, n)
        t = (np.arange(bajo, alto) - centro) / SAMPLE_RATE
        latido = _latido_adaptado(t, rr, ectopico=False)
        if ectopico:
            latido -= _gaussiana(t, -0.16 * np.sqrt(np.sqrt(rr)), 0.025, 0.12)
        señal[bajo:alto] += latido
    tiempos = np.array(instantes)
    return _registro(
        tiempos, np.array(ectopicos), señal, lambda t: lpm if 300.0 <= t < 900.0 else 70.0
    )


@pytest.mark.parametrize("lpm", [130.0, 140.0])
def test_un_supraventricular_prematuro_en_taquicardia_se_informa(
    lpm: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Desde ~125 lpm la ventana del score se recortaba con el R-R esperado y
    dejaba afuera la T del latido anterior, que es lo único que delata a un
    supraventricular prematuro con el QRS normal: no podía pasar el umbral solo
    por la prematuridad. Un prematuro conserva el lado izquierdo entero. La
    taquicardia sinusal sola sigue sin marcarse (`test_rampa_de_esfuerzo...`)."""
    registro = _supraventriculares_en_taquicardia(lpm)
    corrida = _por_bloques(registro, monkeypatch)

    assert registro.ectopicos.size >= 100
    assert _ectopicos_marcados(corrida, registro).mean() >= 0.6
    assert _normales_marcados(corrida, registro) <= 0.01 * registro.latidos.size


@pytest.mark.parametrize("despues_cada", [None, 40])
def test_un_foco_que_fue_dominante_diez_minutos_tiene_su_encabezado(
    despues_cada: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diez minutos de N V V —el foco es la dominante— y después sinusal, sin
    ventriculares o con uno cada 40. Los miembros del foco no podían puntuar
    mientras era la dominante: sin 30 puntuados después nunca tenía encabezado
    (o lo tenía media hora tarde). Y la forma normal, puntuada contra la V,
    calificaba como foco y su encabezado quedaba en la base, congelado. Al
    cambiar la dominante (`morphology.hand_over_dominance`), el foco se juzga
    por su centroide contra la normal y la normal deja de ser un foco: en la
    base queda solo el del ventricular, con todos sus miembros."""
    registro = _arranque_en_bigeminismo(600.0, 1500.0, despues_cada=despues_cada)
    corrida = _por_bloques(registro, monkeypatch)

    primero = morphology.dominant_template(corrida.bancos[0])
    assert primero is not None
    miembros = corrida.latidos[corrida.plantillas == primero.cluster_id]
    assert _cerca(miembros, registro.ectopicos).mean() >= 0.9  # arrancó dominante
    final = morphology.dominant_template(corrida.banco)
    assert final is not None and final.cluster_id != primero.cluster_id
    base = corrida.en_la_base()
    assert set(base) == {primero.cluster_id}
    assert base[primero.cluster_id] == pytest.approx(registro.ectopicos.size, rel=0.05)


@pytest.mark.usefixtures("ml_engine")
async def test_la_base_da_de_baja_el_encabezado_de_la_forma_normal(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """Por la ingesta, con los bloques de producción: diez minutos de N V V y
    después sinusal. Mientras el ventricular era la dominante, la forma normal
    se informó como foco; cuando recupera el lugar su encabezado se da de baja
    (`PipelineResult.retracted_headers`) y queda el del ventricular."""
    from sqlalchemy import select

    from app.db.models.ecg_event import ECGEvent
    from tests.test_ml_blocks import _mundo
    from tests.test_ml_ingest import finalizar

    monkeypatch.setattr(settings, "ml_analysis_block_seconds", float(BLOCK_S))
    monkeypatch.setattr(settings, "ml_analysis_context_seconds", float(CONTEXT_S))
    monkeypatch.setattr(settings, "ml_analysis_lookahead_seconds", float(LOOKAHEAD_S))
    registro = _arranque_en_bigeminismo(600.0, 1500.0, despues_cada=None)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    for lote in np.array_split(np.arange(registro.señal.size), 10):
        await chaleco.enviar(registro.señal[lote], registro.flags[lote])
    await finalizar(db, monkeypatch, study.id)

    filas = (
        await db.scalars(
            select(ECGEvent).where(
                ECGEvent.study_id == study.id,
                ECGEvent.dedupe_key.like("cluster:%"),
            )
        )
    ).all()
    vivas = [fila for fila in filas if fila.deleted_at is None]
    dadas_de_baja = [fila for fila in filas if fila.deleted_at is not None]
    (foco,) = vivas
    assert foco.event_metadata["beatCount"] == pytest.approx(registro.ectopicos.size, rel=0.05)
    assert dadas_de_baja, "la forma normal se había informado mientras no era la dominante"


def test_lo_que_queda_en_la_base_es_lo_que_el_banco_informa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La persistencia solo hace upsert de los encabezados que se emiten: si uno
    dejara de emitirse, quedaría en la base con un conteo viejo. Un estudio en
    curso con un banco de antes de los contadores —se le sacan a los 900 s,
    como si se hubiera guardado con la versión anterior— ya tiene escrito el
    encabezado espurio de la primera taquicardia: se lo sigue actualizando, y
    la segunda, de 7 minutos, no lo congela. En un estudio nuevo la taquicardia
    nunca tiene encabezado. En los dos, cada encabezado de la base tiene el
    conteo de su plantilla al final."""
    escenario = Scenario(
        minutes=45.0,
        bpm=60.0,
        ectopic_every=12,
        tachy=((600.0, 60.0, 155.0), (1500.0, 420.0, 155.0)),
        pauses=(),
        lead_off=((300.0, 30.0),),
        lead_off_flat=False,
        noise_uv=8.0,
        seed=7,
    )
    señal, flags, verdad = synth_scenario(escenario)
    registro = Registro(
        señal=señal.astype(np.float32),
        flags=flags,
        latidos=verdad.rpeaks,
        ectopicos=verdad.ectopic,
        en_taquicardia=np.zeros(verdad.ectopic.size, dtype=bool),
    )

    def a_banco_viejo(fin: int, banco: TemplateBank) -> TemplateBank:
        if fin != 900 * SAMPLE_RATE:
            return banco
        estado, blob = bank_to_state(banco)
        for item in estado["templates"]:
            for clave in ("scoredCount", "anomalousCount", "reported"):
                item.pop(clave, None)
        return bank_from_state(estado, blob, model_version=banco.model_version)

    def al_dia(corrida: Corrida) -> dict[int, int]:
        conteos = {t.cluster_id: t.count for t in corrida.banco.templates}
        base = corrida.en_la_base()
        assert base == {cluster: conteos[cluster] for cluster in base}, (base, conteos)
        return {cluster: conteos[cluster] for cluster in base}

    nuevo = _por_bloques(registro, monkeypatch)
    (foco,) = al_dia(nuevo)  # solo el ventricular
    miembros = nuevo.latidos[nuevo.plantillas == foco]
    assert _cerca(miembros, registro.ectopicos).mean() >= 0.9

    viejo = _por_bloques(registro, monkeypatch, al_cerrar=a_banco_viejo)
    heredados = al_dia(viejo)
    assert len(heredados) == 2  # el ventricular y el de la taquicardia que ya estaba escrito


# --------------------------------------------------------------------------- #
# Lo que no se puede romper
# --------------------------------------------------------------------------- #


def test_reanalizar_un_bloque_ya_plegado_no_suma_nada() -> None:
    """La red de seguridad del banco (`fold_key`) cubre los contadores nuevos:
    un bloque que se reintenta no vuelve a sumar ni sus anómalos ni su
    frecuencia, y deja los mismos `cluster_id`."""
    registro = _del_e2e(5.0, (60.0, 240.0, 150.0))
    config = _config()
    vacio = pipeline.empty_bank(config)
    primero = pipeline.analyze_batch(
        registro.señal,
        registro.flags,
        start_sample_index=0,
        bank=vacio,
        config=config,
        fold_key="b",
    )
    otra_vez = pipeline.analyze_batch(
        registro.señal,
        registro.flags,
        start_sample_index=0,
        bank=primero.bank,
        config=config,
        fold_key="b",
    )

    def huella(banco: TemplateBank) -> list[tuple[int, int, int, float, int]]:
        return [
            (
                t.cluster_id,
                t.count,
                t.anomalous_count,
                round(t.expected_rr_sum, 6),
                t.expected_rr_beats,
            )
            for t in banco.templates
        ]

    assert huella(otra_vez.bank) == huella(primero.bank)
    assert sum(t.anomalous_count for t in primero.bank.templates) > 0
    dominante = morphology.dominant_template(primero.bank)
    assert dominante is not None and dominante.expected_rr_beats > 0


def test_los_cluster_id_no_se_renumeran(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada plantilla conserva su id y su primera aparición de bloque en
    bloque: los `ecg_event` ya escritos con ese id siguen siendo válidos."""
    registro = _modelo_adaptado(RAMPA, 600.0)
    corrida = _por_bloques(registro, monkeypatch)

    for antes, despues in zip(corrida.bancos, corrida.bancos[1:], strict=False):
        previos = {t.cluster_id: t.first_sample for t in antes.templates}
        actuales = {t.cluster_id: t.first_sample for t in despues.templates}
        assert previos.items() <= actuales.items()
        assert despues.next_cluster_id >= antes.next_cluster_id
