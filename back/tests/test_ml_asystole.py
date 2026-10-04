"""Asistolias que el gate de calidad tapaba: la regla de pausa por hueco quieto.

Una asistolia larga deja ventanas de 10 s enteras sin un QRS, el gate las
rechaza (pSQI, kSQI, basSQI, `flatline`, `no_beats`) y el R-R que la cruza
queda inválido: el motor no informaba ninguna pausa ni le avisaba al paciente.
`app/ml/quiet_gap.py` la informa cuando el hueco está demostrablemente quieto
contra los latidos del propio paciente, sin abrir el gate al ruido.

Tres grupos de tests:

- Asistolias de 3 a 60 s sobre líneas de base distintas (limpia, 25 y 50 µV
  de ruido, red de 0,5 mV): una pausa crítica, con un solo aviso. El barrido
  entero corre con `-m slow`. Y las que la primera versión de la regla todavía
  perdía: con las P de un bloqueo AV (paroxístico o completo), cerradas por un
  escape de otra amplitud, con un transitorio suelto adentro, de dos minutos o
  más, después de una T tardía.
- Lo que **no** es una pausa: ruido que tapa latidos, un hueco ruidoso,
  electrodo despegado, saturación, empalmes, el final o el principio de una
  corrida, latidos atenuados por pérdida de contacto o alrededor del umbral, y
  latidos normales chicos al lado de extrasístoles grandes.
- Los bordes de bloque del cursor (`processing.append_ml_analysis`): la
  asistolia que cruza el borde, la que cae en el contexto o en el contexto
  derecho, la más larga que el contexto derecho y la de la cola de una
  corrida cerrada. Por la base, con los bloques de producción: un evento, un
  aviso y un push por asistolia.

Las señales llevan 8 µV de ruido como mínimo, el de `ecg_synth` y de la
verificación e2e: con ruido nulo exacto la ventana es `flatline` (un corto o un
ADC congelado), y por ahí la regla no infiere nada a propósito.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt
from sqlalchemy import select

from app.core.config import settings
from app.db.models.alert import Alert
from app.db.models.ecg_event import ECGEventSeverity, ECGEventType
from app.ml import pipeline
from app.ml.arrhythmia import PAUSE_ALERT, detect_rhythm
from app.ml.contracts import Finding
from app.ml.decompression import FLAG_ADC_SATURATED, FLAG_LEAD_OFF, FLAG_R_PEAK
from app.ml.hrv import build_rr
from app.ml.quality import assess_quality, exclude_splices
from app.ml.quiet_gap import GapEvidence, refine_pauses
from app.ml.rpeak_detection import (
    clean_signal,
    compensate_firmware_peaks,
    detect_rpeaks,
    firmware_rpeaks,
)
from tests.ecg_synth import FIRMWARE_R_PEAK_LAG_MS, SAMPLE_RATE
from tests.synthetic_ecg import synthetic_ecg
from tests.test_ml_blocks import _ecg, _eventos_del_motor, _latidos, _mundo
from tests.test_ml_ingest import finalizar

SR = SAMPLE_RATE
#: El R que abre la asistolia: fuera de la grilla de 10 s de las ventanas.
ABRE_S = 120.3
#: Señal después del R que la cierra: referencia de los dos lados.
DESPUES_S = 60.0
#: Las líneas de base del barrido. 8 µV es el ruido de `ecg_synth` y del e2e.
BASES: dict[str, dict[str, float]] = {
    "limpia": {"noise_mv": 0.008},
    "ruido_25uv": {"noise_mv": 0.025},
    "ruido_50uv": {"noise_mv": 0.050},
    "red_0_5mv": {"noise_mv": 0.008, "mains_mv": 0.5},
}
#: Lo que tarda el detector del MCU en marcar el R (`ecg_synth`).
LAG = int(FIRMWARE_R_PEAK_LAG_MS * SR / 1000.0)


# --------------------------------------------------------------------------- #
# Señal
# --------------------------------------------------------------------------- #


def _latidos_con_hueco(hueco_s: float, *, abre_s: float = ABRE_S, lpm: float = 60.0) -> np.ndarray:
    """Latidos a `lpm` con una asistolia de `hueco_s` después del R de `abre_s`."""
    rr = 60.0 / lpm
    antes = np.arange(abre_s, 0.6, -rr)[::-1]
    despues = np.arange(abre_s + hueco_s, abre_s + hueco_s + DESPUES_S - 1.0, rr)
    return np.concatenate((antes, despues))


def _flags_del_firmware(latidos: np.ndarray, n: int) -> np.ndarray:
    """`FLAG_R_PEAK` donde lo pone el MCU: 250 ms después de cada R."""
    flags = np.zeros(n, dtype=np.uint8)
    posiciones = np.round(latidos * SR).astype(np.int64) + LAG
    flags[posiciones[posiciones < n]] |= FLAG_R_PEAK
    return flags


def _asistolia(
    hueco_s: float,
    *,
    base: str = "limpia",
    firmware: bool = True,
    abre_s: float = ABRE_S,
    duracion_s: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Señal, flags y latidos de un registro con una asistolia de `hueco_s`."""
    latidos = _latidos_con_hueco(hueco_s, abre_s=abre_s)
    duracion = duracion_s if duracion_s is not None else abre_s + hueco_s + DESPUES_S
    latidos = latidos[latidos < duracion - 1.0]
    señal = synthetic_ecg(latidos, duracion, SR, **BASES[base])
    flags = (
        _flags_del_firmware(latidos, señal.size)
        if firmware
        else np.zeros(señal.size, dtype=np.uint8)
    )
    return señal, flags, latidos


def _ondas_p(instantes: np.ndarray, n: int, *, amplitud: float = 0.12) -> np.ndarray:
    """Ondas P sueltas, sin QRS: las del nodo sinusal en un bloqueo AV."""
    t = np.arange(n) / SR
    ondas = np.zeros(n)
    for instante in instantes:
        bajo, alto = np.searchsorted(t, instante - 0.15), np.searchsorted(t, instante + 0.15)
        ondas[bajo:alto] += amplitud * np.exp(-((t[bajo:alto] - instante) ** 2) / (2 * 0.025**2))
    return ondas.astype(np.float32)


def _tramo(desde_s: float, hasta_s: float) -> slice:
    return slice(int(desde_s * SR), int(hasta_s * SR))


def _escape(instantes: np.ndarray, n: int, *, escala: float) -> np.ndarray:
    """Latidos de escape ventricular: QRS 2,5 veces más ancho y T de 0,4 mV."""
    t = np.arange(n) / SR
    ondas = np.zeros(n)
    for instante in instantes:
        bajo, alto = np.searchsorted(t, instante - 0.4), np.searchsorted(t, instante + 0.8)
        local = t[bajo:alto] - instante
        for centro, amplitud, ancho in (
            (-0.125, -0.10, 0.02),
            (0.0, 1.20, 0.025),
            (0.125, -0.25, 0.02),
        ):
            ondas[bajo:alto] += (
                escala * amplitud * np.exp(-((local - centro) ** 2) / (2 * ancho**2))
            )
        ondas[bajo:alto] += 0.4 * np.exp(-((local - 0.35) ** 2) / (2 * 0.06**2))
    return ondas.astype(np.float32)


def _ecg_con_t(
    latidos: np.ndarray, duracion_s: float, *, t_s: float, t_mv: float, t_ancho_s: float
) -> np.ndarray:
    """Como `synthetic_ecg`, con la T corrida, más grande o más ancha (QT largo)."""
    t = np.arange(int(duracion_s * SR)) / SR
    señal = 0.025 * np.random.default_rng(7).standard_normal(t.size)
    ondas = ((-0.20, 0.12, 0.025), (-0.03, -0.10, 0.008), (0.0, 1.20, 0.010), (0.03, -0.25, 0.008))
    for latido in latidos:
        bajo, alto = np.searchsorted(t, latido - 0.45), np.searchsorted(t, latido + 0.9)
        local = t[bajo:alto] - latido
        for centro, amplitud, ancho in (*ondas, (t_s, t_mv, t_ancho_s)):
            señal[bajo:alto] += amplitud * np.exp(-((local - centro) ** 2) / (2 * ancho**2))
    return señal.astype(np.float32)


# --------------------------------------------------------------------------- #
# Motor
# --------------------------------------------------------------------------- #


def _config() -> pipeline.PipelineConfig:
    return pipeline.build_config(settings, SR)


def _analizar(
    señal: np.ndarray,
    flags: np.ndarray,
    *,
    inicio: int = 0,
    contexto: int = 0,
    derecho: int = 0,
    empalmes: tuple[tuple[int, int], ...] = (),
    conocidos: np.ndarray | None = None,
) -> pipeline.PipelineResult:
    config = _config()
    return pipeline.analyze_batch(
        señal.astype(np.float32),
        flags,
        start_sample_index=inicio,
        bank=pipeline.empty_bank(config),
        config=config,
        fold_key=f"{inicio}",
        context_samples=contexto,
        lookahead_samples=derecho,
        gap_samples=empalmes,
        flags_known=conocidos,
    )


def _pausas(resultado: pipeline.PipelineResult) -> list[Finding]:
    return [hallazgo for hallazgo in resultado.findings if hallazgo.kind == "pause"]


def _ritmo(resultado: pipeline.PipelineResult) -> list[Finding]:
    return [
        hallazgo
        for hallazgo in resultado.findings
        if hallazgo.kind in ("pause", "tachycardia", "bradycardia")
    ]


def _verificar_la_asistolia(pausas: list[Finding], abre_s: float, hueco_s: float) -> Finding:
    """Una sola pausa, de punta a punta del hueco, crítica y con aviso."""
    assert len(pausas) == 1, [(p.start_sample / SR, p.length_samples / SR) for p in pausas]
    (pausa,) = pausas
    assert pausa.start_sample / SR == pytest.approx(abre_s, abs=0.1)
    assert pausa.length_samples / SR == pytest.approx(hueco_s, abs=0.1)
    assert pausa.metadata["pauseSeconds"] == pytest.approx(hueco_s, abs=0.1)
    assert pausa.event_type is ECGEventType.PAUSE
    assert pausa.severity is ECGEventSeverity.CRITICAL
    assert pausa.alert_message == PAUSE_ALERT
    return pausa


# --------------------------------------------------------------------------- #
# Asistolias que se informan
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("base", sorted(BASES))
@pytest.mark.parametrize("hueco_s", [3.0, 12.0, 25.0, 60.0])
def test_una_asistolia_se_informa_como_una_sola_pausa_critica(base: str, hueco_s: float) -> None:
    """Desde 12 s ninguna ventana del hueco es `good`: la pausa sale por la
    regla de hueco quieto, y lo dice en su metadata."""
    señal, flags, _ = _asistolia(hueco_s, base=base)
    resultado = _analizar(señal, flags)

    pausa = _verificar_la_asistolia(_pausas(resultado), ABRE_S, hueco_s)
    assert _ritmo(resultado) == [pausa], "una asistolia no es una bradicardia"
    if hueco_s >= 12.0:
        assert pausa.metadata["quietGap"] is True
        assert float(pausa.metadata["interiorRatio"]) < 0.3
        assert float(pausa.metadata["firstBeatRatio"]) == pytest.approx(1.0, abs=0.2)


@pytest.mark.slow
@pytest.mark.parametrize("firmware", [True, False], ids=["con_flags", "sin_flags"])
@pytest.mark.parametrize("base", sorted(BASES))
def test_barrido_de_asistolias_de_3_a_60_s(base: str, firmware: bool) -> None:
    """El barrido de la medición: cada duración, en dos posiciones contra la
    grilla de ventanas, con y sin los R del firmware."""
    for hueco_s in (3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 45.0, 60.0):
        for abre_s in (ABRE_S, 125.0):
            señal, flags, _ = _asistolia(hueco_s, base=base, firmware=firmware, abre_s=abre_s)
            _verificar_la_asistolia(_pausas(_analizar(señal, flags)), abre_s, hueco_s)


@pytest.mark.parametrize("firmware", [True, False], ids=["con_flags", "sin_flags"])
def test_las_p_de_un_bloqueo_av_no_parten_la_asistolia(firmware: bool) -> None:
    """Las P siguen llegando sin QRS. NeuroKit las marca como R y, sin los
    flags del firmware, las ventanas del hueco quedan `good`: el motor veía
    doce R-R de un segundo y ninguna pausa. Una P mide ~0,1 del QRS, menos que
    `GOOD_BEAT_MIN`, y no corta el hueco."""
    hueco_s = 12.0
    señal, flags, _ = _asistolia(hueco_s, base="ruido_25uv", firmware=firmware)
    señal = señal + _ondas_p(np.arange(ABRE_S + 0.8, ABRE_S + hueco_s - 0.3, 1.0), señal.size)

    _verificar_la_asistolia(_pausas(_analizar(señal, flags)), ABRE_S, hueco_s)


def test_una_pausa_partida_por_un_pico_falso_se_informa_entera() -> None:
    """Sobre el ruido del hueco NeuroKit pone de vez en cuando un R falso: el
    motor informaba una pausa más corta, desde el mismo R, que no avisaba como
    crítica la asistolia entera. La de hueco quieto la reemplaza."""
    hueco_s = 8.0
    señal, flags, _ = _asistolia(hueco_s, base="ruido_50uv")
    # Una deflexión de ~0,1 mV a mitad del hueco: un pico que NeuroKit toma
    # por R pero que mide la décima parte de un QRS.
    señal = señal + _ondas_p(np.array([ABRE_S + 4.0]), señal.size, amplitud=0.1)

    _verificar_la_asistolia(_pausas(_analizar(señal, flags)), ABRE_S, hueco_s)


@pytest.mark.parametrize("abre_s", [ABRE_S, 127.3, 128.3])
@pytest.mark.parametrize("amplitud", [0.20, 0.25])
def test_las_p_grandes_de_un_bloqueo_av_tampoco_parten_la_asistolia(
    amplitud: float, abre_s: float
) -> None:
    """P a 75 lpm de 0,17-0,21 del QRS (en el chaleco y en MIT-BIH hay hasta
    0,39) que caen en la ventana `good` donde vuelven los latidos. Medían más
    que `GOOD_BEAT_MIN` y partían la asistolia en pedazos sin cotas: no salía
    nada. Con los flags del firmware, que no las marca, no cortan."""
    hueco_s = 12.0
    señal, flags, _ = _asistolia(hueco_s, base="ruido_25uv", abre_s=abre_s)
    señal = señal + _ondas_p(
        np.arange(abre_s + 0.8, abre_s + hueco_s - 0.3, 0.8), señal.size, amplitud=amplitud
    )

    _verificar_la_asistolia(_pausas(_analizar(señal, flags)), abre_s, hueco_s)


def test_un_paro_ventricular_en_un_bloqueo_av_completo_se_informa() -> None:
    """Stokes-Adams: escape a 35 lpm con P disociadas en todo el registro.
    NeuroKit marca cada P, el firmware no, y el bSQI deja todas las ventanas
    `marginal`: no había ni un latido `good` de referencia. Los QRS que vieron
    los dos detectores sí lo son."""
    rr = 60.0 / 35.0
    abre_s, hueco_s = 119.7, 12.0
    latidos = np.concatenate(
        (np.arange(abre_s, 0.6, -rr)[::-1], np.arange(abre_s + hueco_s, 239.0, rr))
    )
    señal = synthetic_ecg(latidos, 240.0, SR, noise_mv=0.025)
    ondas_p = np.arange(0.5, 239.0, 0.8)
    ondas_p = ondas_p[np.min(np.abs(ondas_p[:, None] - latidos[None, :]), axis=1) > 0.25]
    señal = señal + _ondas_p(ondas_p, señal.size)
    resultado = _analizar(señal, _flags_del_firmware(latidos, señal.size))

    assert {intervalo.reason for intervalo, _ in resultado.quality_intervals} == {"bsqi"}
    _verificar_la_asistolia(_pausas(resultado), abre_s, hueco_s)


@pytest.mark.parametrize(
    ("escala", "sostenido"),
    [(0.4, True), (3.5, True), (0.4, False), (3.5, False)],
    ids=["chico_sostenido", "grande_sostenido", "chico_suelto", "grande_suelto"],
)
def test_una_asistolia_que_cierra_un_escape_se_informa(escala: float, sostenido: bool) -> None:
    """El latido que cierra la asistolia es un escape ventricular de 0,4 o 3,5
    veces el sinusal: fuera de `BOUND_MIN`-`BOUND_MAX`, no acotaba y la asistolia
    no se informaba de ningún lado. El firmware lo vio: es un latido."""
    hueco_s = 12.0
    cierra = ABRE_S + hueco_s
    antes = np.arange(ABRE_S, 0.6, -1.0)[::-1]
    duracion = cierra + 70.0
    escapes = np.arange(cierra, duracion - 1.0, 60.0 / 35.0) if sostenido else np.array([cierra])
    despues = np.array([]) if sostenido else np.arange(cierra + 1.0, duracion - 1.0, 1.0)
    señal = synthetic_ecg(np.concatenate((antes, despues)), duracion, SR, noise_mv=0.025)
    señal = señal + _escape(escapes, señal.size, escala=escala)
    todos = np.sort(np.concatenate((antes, escapes, despues)))

    _verificar_la_asistolia(
        _pausas(_analizar(señal, _flags_del_firmware(todos, señal.size))), ABRE_S, hueco_s
    )


@pytest.mark.parametrize(
    "evento", ["escape_chico", "escape_grande", "pop"], ids=lambda evento: evento
)
def test_un_transitorio_suelto_no_anula_la_asistolia(evento: str) -> None:
    """Un escape que ningún detector vio, de 0,45 o 3,5 veces el sinusal, o el
    pop de un electrodo, a mitad de una asistolia de 20 s: el interior ya no
    estaba quieto y se descartaba el hueco entero. Ahora lo parte, y los dos
    tramos quietos —antes y después— salen como una sola pausa crítica."""
    hueco_s = 20.0
    señal, flags, _ = _asistolia(hueco_s, base="ruido_25uv")
    medio = ABRE_S + hueco_s / 2
    if evento == "pop":
        señal[int(medio * SR) : int(medio * SR) + 4] += np.float32(1.0)
    else:
        escala = 0.45 if evento == "escape_chico" else 3.5
        señal = señal + _escape(np.array([medio]), señal.size, escala=escala)

    (pausa,) = _pausas(_analizar(señal, flags))
    assert pausa.start_sample / SR == pytest.approx(ABRE_S, abs=0.1)
    assert (pausa.start_sample + pausa.length_samples) / SR == pytest.approx(
        ABRE_S + hueco_s, abs=0.1
    )
    assert pausa.severity is ECGEventSeverity.CRITICAL
    assert pausa.alert_message == PAUSE_ALERT
    assert pausa.metadata["interiorEvents"] == 1


def test_latidos_atenuados_alrededor_del_umbral_no_parten_un_hueco() -> None:
    """MIT-BIH 116 y 208: latidos atenuados a ~0,25 del QRS, con un par apenas
    por encima de `QUIET_MAX`. Esos dos no sobresalen de lo quieto
    (`EVENT_CONTRAST`): no son transitorios sobre una asistolia sino la misma
    señal débil, y el hueco no se parte."""
    señal, flags = _sinusal()
    latidos = np.arange(0.7, 239.0, 1.0)
    debiles = latidos[(latidos > 120.3) & (latidos < 128.3)]
    tramo = _tramo(120.6, 128.6)
    sin_latidos = synthetic_ecg(np.setdiff1d(latidos, debiles), 240.0, SR, noise_mv=0.025)
    escala = np.full(tramo.stop - tramo.start, 0.25, dtype=np.float32)
    for latido in (122.7, 125.7):
        escala[_tramo(latido - 0.3, latido + 0.5).start - tramo.start :][: int(0.8 * SR)] = 0.36
    señal[tramo] = sin_latidos[tramo] + escala * (señal[tramo] - sin_latidos[tramo])
    flags[_tramo(120.6, 128.9)] = 0

    assert _ritmo(_analizar(señal, flags)) == []


@pytest.mark.parametrize("hueco_s", [120.0, 150.0])
def test_una_asistolia_de_dos_minutos_se_informa(hueco_s: float) -> None:
    """La referencia del hueco se tomaba a ±60 s de su centro, que en una
    asistolia de dos minutos es el hueco mismo: desde ~115 s no había contra
    qué medir. Ahora es lo que rodea a sus dos R."""
    señal, flags, _ = _asistolia(hueco_s, base="ruido_25uv")

    _verificar_la_asistolia(_pausas(_analizar(señal, flags)), ABRE_S, hueco_s)


@pytest.mark.parametrize(("abre_s", "hueco_s"), [(230.3, 100.0), (220.3, 110.0), (210.3, 150.0)])
def test_una_asistolia_que_ningun_bloque_ve_entera_avisa_igual(
    abre_s: float, hueco_s: float
) -> None:
    """Con 60 s de contexto y 30 de contexto derecho, ningún bloque lee los dos
    R. El que lee el que la cierra empieza en medio de la corrida y quieto:
    informa la pausa desde el principio de su lectura (`openStart`), crítica."""
    duracion = abre_s + hueco_s + 70.0
    señal, flags, _ = _asistolia(hueco_s, abre_s=abre_s, duracion_s=duracion)
    informados = [hallazgo for bloque in _por_bloques(señal, flags) for hallazgo in bloque]

    (pausa,) = informados
    assert pausa.severity is ECGEventSeverity.CRITICAL
    assert pausa.metadata["openStart"] is True
    assert pausa.start_sample / SR == pytest.approx(240.0, abs=0.01), "el inicio de la lectura"
    assert (pausa.start_sample + pausa.length_samples) / SR == pytest.approx(
        abre_s + hueco_s, abs=0.1
    )
    # De una sola vez, la misma asistolia sale entera.
    _verificar_la_asistolia(_pausas(_analizar(señal, flags)), abre_s, hueco_s)


def test_la_t_de_una_extrasistole_no_borra_la_pausa_que_la_sigue() -> None:
    """Pausa post-extrasistólica de 3,5 s: la T de la extrasístole, de 1,2 mV y
    tardía, cae en el interior con 0,7 del QRS. El veto la tomaba por un latido
    que NeuroKit no vio; en la banda del QRS es lenta y no lo es."""
    extrasistole = 120.3
    latidos = np.concatenate(
        (np.arange(119.7, 0.6, -1.0)[::-1], np.arange(extrasistole + 3.5, 239.0, 1.0))
    )
    señal = synthetic_ecg(latidos, 240.0, SR, noise_mv=0.025).astype(np.float64)
    t = np.arange(señal.size) / SR
    for centro, amplitud, ancho in (
        (-0.125, -0.15, 0.02),
        (0.0, 1.8, 0.025),
        (0.125, -0.375, 0.02),
        (0.44, 1.2, 0.09),
    ):
        señal += amplitud * np.exp(-((t - extrasistole - centro) ** 2) / (2 * ancho**2))
    flags = _flags_del_firmware(np.sort(np.append(latidos, extrasistole)), señal.size)

    (pausa,) = _pausas(_analizar(señal.astype(np.float32), flags))
    assert pausa.start_sample / SR == pytest.approx(extrasistole, abs=0.05)
    assert pausa.length_samples / SR == pytest.approx(3.5, abs=0.05)
    assert pausa.severity is ECGEventSeverity.CRITICAL


@pytest.mark.parametrize("hueco_s", [4.0, 12.0])
def test_una_t_tardia_no_acorta_la_pausa(hueco_s: float) -> None:
    """QT largo: T de 0,6 mV a 0,45 s del R. Pasa `QUIET_MAX` en el interior y
    partía el hueco; la pausa salía desde la T, medio segundo más corta. Es una
    onda lenta (`EVENT_BEAT_QRS`): queda adentro del tramo y no lo acota."""
    rr = 1.3
    latidos = np.concatenate(
        (
            np.arange(ABRE_S, 0.6, -rr)[::-1],
            np.arange(ABRE_S + hueco_s, ABRE_S + hueco_s + 70.0, rr),
        )
    )
    señal = _ecg_con_t(latidos, ABRE_S + hueco_s + 71.0, t_s=0.45, t_mv=0.6, t_ancho_s=0.06)

    _verificar_la_asistolia(
        _pausas(_analizar(señal, _flags_del_firmware(latidos, señal.size))), ABRE_S, hueco_s
    )


# --------------------------------------------------------------------------- #
# Lo que no es una pausa
# --------------------------------------------------------------------------- #


def _sinusal(duracion_s: float = 240.0, *, firmware: bool = True) -> tuple[np.ndarray, np.ndarray]:
    latidos = np.arange(0.7, duracion_s - 1.0, 1.0)
    señal = synthetic_ecg(latidos, duracion_s, SR, noise_mv=0.025)
    flags = (
        _flags_del_firmware(latidos, señal.size)
        if firmware
        else np.zeros(señal.size, dtype=np.uint8)
    )
    return señal, flags


def _movimiento(n: int, amplitud_mv: float, *, seed: int = 3) -> np.ndarray:
    """Artefacto de movimiento: ruido de 1-8 Hz, la banda del QRS y la deriva."""
    ruido = np.random.default_rng(seed).standard_normal(n)
    filtrado = sosfiltfilt(butter(2, [1.0, 8.0], btype="band", fs=SR, output="sos"), ruido)
    return (filtrado / filtrado.std() * amplitud_mv).astype(np.float32)


@pytest.mark.parametrize("firmware", [True, False], ids=["con_flags", "sin_flags"])
def test_el_ruido_que_tapa_latidos_no_es_una_pausa(firmware: bool) -> None:
    """Ráfagas de ruido blanco y de movimiento encima de un ritmo normal. El
    gate rechaza las ventanas; si NeuroKit pierde latidos ahí, el hueco no
    está quieto y no se informa nada."""
    señal, flags = _sinusal(firmware=firmware)
    rng = np.random.default_rng(5)
    rafaga = _tramo(120.3, 130.3)
    señal[rafaga] += (1.0 * rng.standard_normal(5000)).astype(np.float32)
    movimiento = _tramo(160.3, 175.3)
    señal[movimiento] += _movimiento(señal.size, 3.0)[movimiento]

    assert _ritmo(_analizar(señal, flags)) == []


def test_un_hueco_sin_latidos_pero_con_ruido_no_se_puede_afirmar() -> None:
    """Los latidos faltan de verdad, pero el hueco tiene 0,3 mV de ruido: no
    hay forma de saber si debajo hubo QRS. El gate existe justamente para no
    afirmar nada sobre esto."""
    hueco_s = 12.0
    señal, flags, _ = _asistolia(hueco_s)
    tramo = _tramo(ABRE_S + 0.6, ABRE_S + hueco_s - 0.4)
    ruido = 0.3 * np.random.default_rng(9).standard_normal(tramo.stop - tramo.start)
    señal[tramo] += ruido.astype(np.float32)

    assert _pausas(_analizar(señal, flags)) == []


@pytest.mark.parametrize(
    ("bit", "nivel_mv"),
    [(FLAG_LEAD_OFF, 400.0), (FLAG_ADC_SATURATED, 395.0), (FLAG_LEAD_OFF, None)],
    ids=["electrodo_despegado", "saturacion", "despegado_sin_riel"],
)
def test_lo_que_el_hardware_marca_como_sin_senal_no_es_una_pausa(
    bit: int, nivel_mv: float | None
) -> None:
    """Doce segundos sin latidos porque el electrodo se despegó (o el AFE se
    fue al riel): faltan muestras, no latidos. Sin riel, el tramo marcado
    `LEAD_OFF` está tan quieto como una asistolia, y es el bit el que manda."""
    señal, flags = _sinusal()
    tramo = _tramo(120.6, 132.6)
    quieto = synthetic_ecg(np.array([]), 240.0, SR, noise_mv=0.025, seed=11)
    señal[tramo] = quieto[tramo] if nivel_mv is None else nivel_mv
    flags[tramo] = bit

    assert _ritmo(_analizar(señal, flags)) == []


def test_el_riel_sin_flags_de_un_segmento_viejo_tampoco() -> None:
    """Un lote ingerido antes de que se archivaran los flags trae el riel sin
    `LEAD_OFF`: la ventana es `flatline`, y por ahí la regla no infiere nada."""
    señal, flags = _sinusal(firmware=False)
    señal[_tramo(120.6, 132.6)] = 400.0

    assert _ritmo(_analizar(señal, flags)) == []


def test_una_asistolia_con_un_empalme_adentro_no_se_informa() -> None:
    """Un `frame_gap` en medio del hueco es adquisición perdida: los latidos de
    ese tramo pueden haber existido. Sin el empalme, la misma señal sí da la
    pausa."""
    hueco_s = 12.0
    señal, flags, _ = _asistolia(hueco_s)
    empalme = ((int((ABRE_S + 6.0) * SR), 3 * SR),)

    assert _pausas(_analizar(señal, flags, empalmes=empalme)) == []
    _verificar_la_asistolia(_pausas(_analizar(señal, flags)), ABRE_S, hueco_s)


@pytest.mark.parametrize("donde", ["final", "principio"])
def test_el_borde_de_una_corrida_no_cierra_ni_abre_una_pausa(donde: str) -> None:
    """La lectura termina —o empieza— con 15 s quietos. Sin un R del otro lado
    no hay R-R, y la regla no inventa el que falta: el final de la corrida no
    cierra una pausa ni su principio la abre (una asistolia que sigue hasta el
    final de la corrida queda sin informar, ver `quiet_gap`)."""
    señal, flags = _sinusal(150.0)
    quieto = _tramo(135.3, 150.0) if donde == "final" else _tramo(0.0, 15.3)
    base = synthetic_ecg(np.array([]), 150.0, SR, noise_mv=0.025, seed=11)
    señal[quieto] = base[quieto]
    flags[quieto] = 0

    assert _ritmo(_analizar(señal, flags)) == []


def test_latidos_atenuados_por_perdida_de_contacto_no_son_una_asistolia() -> None:
    """El caso real de `aviso_ll_ra`: diez segundos de latidos a un décimo de su
    amplitud, que ni NeuroKit ni el firmware ven, con la red que sube porque el
    electrodo perdió contacto. El interior está quieto; el contacto, no."""
    señal, flags = _sinusal()
    latidos = np.arange(0.7, 239.0, 1.0)
    atenuados = latidos[(latidos > 120.3) & (latidos < 130.3)]
    tramo = _tramo(120.6, 130.6)
    sin_latidos = synthetic_ecg(np.setdiff1d(latidos, atenuados), 240.0, SR, noise_mv=0.025)
    señal[tramo] = sin_latidos[tramo] + 0.1 * (señal[tramo] - sin_latidos[tramo])
    t = np.arange(tramo.start, tramo.stop) / SR
    señal[tramo] += (0.5 * np.sin(2 * np.pi * 50.2 * t)).astype(np.float32)
    flags[_tramo(120.6, 130.9)] = 0

    assert _ritmo(_analizar(señal, flags)) == []


def test_si_el_firmware_vio_latidos_en_el_hueco_no_es_una_asistolia() -> None:
    """Latidos atenuados a un décimo **sin** cambio de contacto: por amplitud no
    se distinguen de una asistolia. Si el firmware los marcó, el hueco no es
    quieto para él y la regla no lo informa."""
    señal, flags = _sinusal()
    latidos = np.arange(0.7, 239.0, 1.0)
    atenuados = latidos[(latidos > 120.3) & (latidos < 130.3)]
    tramo = _tramo(120.6, 130.6)
    sin_latidos = synthetic_ecg(np.setdiff1d(latidos, atenuados), 240.0, SR, noise_mv=0.025)
    señal[tramo] = sin_latidos[tramo] + 0.1 * (señal[tramo] - sin_latidos[tramo])

    assert _ritmo(_analizar(señal, flags)) == []


def _extrasistoles(instantes: np.ndarray, n: int, *, amplitud: float) -> np.ndarray:
    """Extrasístoles grandes y anchas, con su T invertida."""
    t = np.arange(n) / SR
    ondas = np.zeros(n)
    for instante in instantes:
        bajo, alto = np.searchsorted(t, instante - 0.4), np.searchsorted(t, instante + 0.7)
        local = t[bajo:alto] - instante
        ondas[bajo:alto] += amplitud * np.exp(-(local**2) / (2 * 0.035**2))
        ondas[bajo:alto] += -0.12 * amplitud * np.exp(-((local - 0.32) ** 2) / (2 * 0.07**2))
    return ondas.astype(np.float32)


@pytest.mark.parametrize("firmware", ["sin_flags", "pierde_los_normales"])
def test_unas_extrasistoles_grandes_no_vuelven_quietos_a_los_latidos_normales(
    firmware: str,
) -> None:
    """Cuadrigeminismo con extrasístoles de 4 veces el QRS y deriva de
    0,5 mV. Con la referencia en el percentil 90, un 25 % de extrasístoles era
    "el latido del paciente": los normales del medio medían menos que
    `QUIET_MAX` y dos extrasístoles acotaban una pausa CRITICAL falsa (en
    MIT-BIH 114, de 12,7 s con diez latidos adentro). La referencia es la
    población dominante."""
    duracion = 300.0
    normales = np.arange(0.7, duracion - 1.0, 60.0 / 70.0)
    sinusales = np.array([b for i, b in enumerate(normales) if i % 4 != 3])
    extras = np.array([normales[i - 1] + 0.55 for i in range(len(normales)) if i % 4 == 3])
    señal = synthetic_ecg(sinusales, duracion, SR, noise_mv=0.01)
    señal = señal + _extrasistoles(extras, señal.size, amplitud=5.0)
    t = np.arange(señal.size) / SR
    deriva = (t >= 150.0) & (t < 210.0)
    señal[deriva] += (0.5 * np.sin(2 * np.pi * 0.25 * t[deriva])).astype(np.float32)
    if firmware == "sin_flags":
        flags = np.zeros(señal.size, dtype=np.uint8)
        conocidos: np.ndarray | None = np.zeros(señal.size, dtype=bool)
    else:
        todos = np.sort(np.concatenate((sinusales, extras)))
        perdidos = np.isin(todos, sinusales) & (todos >= 150.0) & (todos < 210.0)
        flags, conocidos = _flags_del_firmware(todos[~perdidos], señal.size), None

    assert _pausas(_analizar(señal, flags, conocidos=conocidos)) == []


def test_una_asistolia_no_se_pinta_ademas_como_ruido() -> None:
    """Las ventanas sin QRS de una asistolia salen `bad`/pSQI, y se pintaban
    como `noise_burst` —"los electrodos estaban bien y no se pudo leer"— encima
    de la pausa crítica que las explica. Siguen `bad` en los intervalos de
    calidad: solo dejan de ser un hallazgo de ruido."""
    hueco_s = 25.0
    resultado = _analizar(*_asistolia(hueco_s)[:2])

    (pausa,) = _pausas(resultado)
    desde, hasta = pausa.start_sample, pausa.start_sample + pausa.length_samples
    assert [
        hallazgo
        for hallazgo in resultado.findings
        if hallazgo.event_type is ECGEventType.NOISE
        and hallazgo.start_sample < hasta
        and hallazgo.start_sample + hallazgo.length_samples > desde
    ] == []
    assert any(
        intervalo.reason == "psqi" and desde <= intervalo.start_sample < hasta
        for intervalo, _ in resultado.quality_intervals
    )


# --------------------------------------------------------------------------- #
# La depuración de las pausas del motor
# --------------------------------------------------------------------------- #


def _evidencia(señal: np.ndarray, flags: np.ndarray, picos: np.ndarray) -> GapEvidence:
    config = _config()
    limpia = clean_signal(señal, SR)
    firmware = compensate_firmware_peaks(
        firmware_rpeaks(flags),
        lag_samples=config.quality.firmware_lag_samples,
        refractory_samples=config.quality.firmware_refractory_samples,
    )
    reporte = assess_quality(
        señal, flags, limpia, firmware, picos, sample_rate=SR, thresholds=config.quality
    )
    return GapEvidence(
        signal=señal,
        flags=flags,
        cleaned=limpia,
        rpeaks=picos,
        firmware_peaks=firmware,
        report=reporte,
        analyzable=reporte.analyzable,
        splice_free=exclude_splices(np.ones(señal.size, dtype=bool), (), SR),
        sample_rate=SR,
        tolerance_samples=config.quality.bsqi_tolerance_samples,
    )


def test_una_pausa_del_motor_con_un_latido_adentro_se_descarta() -> None:
    """NeuroKit no vio dos latidos seguidos en ventanas buenas (en MIT-BIH, los
    bloqueos de rama del 207): el R-R de 3 s es válido y el motor avisaba una
    pausa que no existió. El interior tiene dos QRS enteros."""
    señal, flags = _sinusal(firmware=False)
    picos = detect_rpeaks(clean_signal(señal, SR), SR)
    perdidos = (picos > 120.5 * SR) & (picos < 122.5 * SR)
    assert int(perdidos.sum()) == 2
    vistos = picos[~perdidos]
    evidencia = _evidencia(señal, flags, vistos)
    del_motor = detect_rhythm(build_rr(vistos, evidencia.analyzable, SR), _config().rhythm, SR)
    assert [h.kind for h in del_motor] == ["pause"], "el R-R de 3 s es una pausa válida"

    assert refine_pauses(del_motor, evidencia, pause_seconds=2.5) == []


def test_una_pausa_real_del_motor_queda_como_estaba() -> None:
    """La pausa de 3 s que el motor ya veía —R-R válido, interior quieto— no se
    toca: ni se descarta ni se vuelve de hueco quieto."""
    señal, flags, _ = _asistolia(3.0)
    picos = detect_rpeaks(clean_signal(señal, SR), SR)
    evidencia = _evidencia(señal, flags, picos)
    del_motor = detect_rhythm(build_rr(picos, evidencia.analyzable, SR), _config().rhythm, SR)
    assert [h.kind for h in del_motor] == ["pause"]

    assert refine_pauses(del_motor, evidencia, pause_seconds=2.5) == del_motor


# --------------------------------------------------------------------------- #
# Bordes de bloque (el recorrido de `processing.append_ml_analysis`, sin base)
# --------------------------------------------------------------------------- #

BLOQUE = 300 * SR
CONTEXTO = 60 * SR
DERECHO = 30 * SR
REFRACTARIO = int(settings.ml_episode_refractory_seconds * SR)


def _por_bloques(señal: np.ndarray, flags: np.ndarray) -> list[list[Finding]]:
    """Los hallazgos de cada bloque de una corrida cerrada, en coordenadas absolutas.

    Bloques de 300 s con 60 s de contexto y 30 de contexto derecho, y la cola
    con lo que quede: el recorrido de `_pending_blocks` con la corrida cerrada.
    """
    n = señal.size
    por_bloque: list[list[Finding]] = []
    for inicio in range(0, n, BLOQUE):
        fin = min(inicio + BLOQUE, n)
        lectura, hasta = max(inicio - CONTEXTO, 0), min(fin + DERECHO, n)
        resultado = _analizar(
            señal[lectura:hasta],
            flags[lectura:hasta],
            inicio=lectura,
            contexto=inicio - lectura,
            derecho=hasta - fin,
        )
        por_bloque.append(_ritmo(resultado))
    return por_bloque


@pytest.mark.parametrize(
    ("abre_s", "hueco_s", "duracion_s"),
    [
        (295.3, 12.0, 420.0),  # cruza el borde: el primer bloque la ve en su contexto derecho
        (302.3, 12.0, 420.0),  # entera en el contexto derecho del primero
        (270.3, 12.0, 420.0),  # en la parte nueva del primero y el contexto del segundo
        (285.3, 50.0, 420.0),  # el R que la cierra cae después del contexto derecho
        (360.3, 25.0, 460.0),  # en la cola de la corrida, sin contexto derecho
    ],
    ids=["cruza_el_borde", "contexto_derecho", "contexto", "mas_larga_que_el_derecho", "cola"],
)
def test_la_asistolia_es_un_solo_evento_caiga_donde_caiga_el_borde(
    abre_s: float, hueco_s: float, duracion_s: float
) -> None:
    """Lo que informa cada bloque, con la regla con que la persistencia empalma
    (`ml_persistence._stitch`): mismo tipo, y solapados o a menos de la
    refractariedad. Todo lo que se informa de la asistolia es un solo evento,
    crítico, que la cubre de punta a punta; y nada más."""
    señal, flags, _ = _asistolia(hueco_s, abre_s=abre_s, duracion_s=duracion_s)
    informados = [hallazgo for bloque in _por_bloques(señal, flags) for hallazgo in bloque]

    assert informados, "ningún bloque informó la asistolia"
    assert {h.kind for h in informados} == {"pause"}
    assert {h.severity for h in informados} == {ECGEventSeverity.CRITICAL}
    inicio = min(h.start_sample for h in informados)
    fin = max(h.start_sample + h.length_samples for h in informados)
    for hallazgo in informados:
        assert hallazgo.start_sample <= fin + REFRACTARIO
        assert hallazgo.start_sample + hallazgo.length_samples >= inicio - REFRACTARIO
    assert inicio / SR == pytest.approx(abre_s, abs=0.1)
    assert (fin - inicio) / SR == pytest.approx(hueco_s, abs=0.1)


# --------------------------------------------------------------------------- #
# Por la base: un evento, un aviso, un push
# --------------------------------------------------------------------------- #


@pytest.fixture
def bloques_de_produccion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ml_analysis_block_seconds", 300.0)
    monkeypatch.setattr(settings, "ml_analysis_context_seconds", 60.0)
    monkeypatch.setattr(settings, "ml_analysis_lookahead_seconds", 30.0)


@pytest.mark.usefixtures("ml_engine", "bloques_de_produccion")
async def test_una_asistolia_sobre_el_borde_de_un_bloque_avisa_una_sola_vez(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """Veinticinco segundos de asistolia que cruzan el borde de los 300 s, en
    lotes de un minuto como los de una corrida abierta. La ventana de 300-310 s
    queda sin un QRS: sin la regla no salía ninguna pausa. El primer bloque la
    escribe desde su contexto derecho y avisa; la cola la vuelve a ver desde su
    contexto y la persistencia la empalma sin un segundo aviso."""
    latidos = [t for t in _latidos([(420.0, 60.0)]) if not 290.6 < t < 315.4]
    señal, flags = _ecg(latidos, 420.0)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    for lote in np.array_split(np.arange(señal.size), 7):
        await chaleco.enviar(señal[lote], flags[lote])
    await finalizar(db, monkeypatch, study.id)

    (pausa,) = await _eventos_del_motor(db, study.id, "pause")
    assert pausa.severity is ECGEventSeverity.CRITICAL
    assert pausa.event_metadata["quietGap"] is True
    assert pausa.event_metadata["pauseSeconds"] == pytest.approx(25.0, abs=0.05)
    assert pausa.event_metadata["startSampleIndex"] / SR == pytest.approx(290.5, abs=0.05)
    assert pausa.event_metadata["sampleCount"] / SR == pytest.approx(25.0, abs=0.05)
    avisos = (await db.scalars(select(Alert).where(Alert.event_id == pausa.id))).all()
    assert len(avisos) == 1
    assert len([p for p in sent_pushes if p[1].data.get("kind") == "pause"]) == 1


@pytest.mark.usefixtures("ml_engine", "bloques_de_produccion")
async def test_una_asistolia_que_ningun_bloque_ve_entera_avisa_una_vez(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """Cien segundos de asistolia desde los 230,5 s: el primer bloque lee hasta
    los 330 s y no ve el R que la cierra; el segundo empieza a leer en los 240,
    con la asistolia ya en curso. Sin el tramo abierto no salía nada; ahora el
    segundo la informa desde el principio de su lectura, con un aviso."""
    latidos = [t for t in _latidos([(460.0, 60.0)]) if not 230.6 < t < 330.4]
    señal, flags = _ecg(latidos, 460.0)
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    for lote in np.array_split(np.arange(señal.size), 8):
        await chaleco.enviar(señal[lote], flags[lote])
    await finalizar(db, monkeypatch, study.id)

    (pausa,) = await _eventos_del_motor(db, study.id, "pause")
    assert pausa.severity is ECGEventSeverity.CRITICAL
    assert pausa.event_metadata["openStart"] is True
    assert pausa.event_metadata["startSampleIndex"] / SR == pytest.approx(240.0, abs=0.01)
    fin = pausa.event_metadata["startSampleIndex"] + pausa.event_metadata["sampleCount"]
    assert fin / SR == pytest.approx(330.5, abs=0.05)
    avisos = (await db.scalars(select(Alert).where(Alert.event_id == pausa.id))).all()
    assert len(avisos) == 1
    assert len([p for p in sent_pushes if p[1].data.get("kind") == "pause"]) == 1


@pytest.mark.usefixtures("ml_engine", "bloques_de_produccion")
async def test_entre_dos_corridas_no_se_infiere_una_pausa(
    client, s3, db, monkeypatch, sent_pushes, make_patient, make_device, make_study
) -> None:
    """La primera corrida termina con 10 s quietos y la segunda empieza con 10
    s quietos. En el buffer quedan pegadas: 20 s sin un QRS entre dos R
    creíbles. Pero entre las dos hubo un corte de grabación, no una asistolia, y
    ningún bloque cruza el borde de una corrida."""
    primera, primeros = _ecg([t for t in _latidos([(200.0, 60.0)]) if t < 190.0], 200.0)
    segunda, segundos = _ecg([t for t in _latidos([(200.0, 60.0)]) if t > 10.0], 200.0, seed=3)
    pegadas = _analizar(np.concatenate((primera, segunda)), np.concatenate((primeros, segundos)))
    assert _pausas(pegadas), "pegadas en un solo bloque, la regla sí vería una pausa"
    chaleco, study = await _mundo(client, db, make_patient, make_device, make_study)
    await chaleco.enviar(primera, primeros)
    await chaleco.enviar(segunda, segundos, corrida_nueva=True)
    await finalizar(db, monkeypatch, study.id)

    assert await _eventos_del_motor(db, study.id, "pause") == []
    assert [p for p in sent_pushes if p[1].data.get("kind") == "pause"] == []
