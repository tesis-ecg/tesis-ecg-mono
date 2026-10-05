"""Mediciones de intervalos por bloque: QT, QTc y amplitud R. **Experimentales.**

Este módulo mide, sobre un bloque de análisis y solo en ventanas de calidad
GOOD, la mediana por latido de:

- QT = R_onset → T_offset (la definición del plan),
- QTc de Fridericia = QT / RR_previo^(1/3), con el RR en segundos,
- amplitud R = señal[pico R] − señal[R_onset], en mV (R_onset es el nadir de la
  Q cuando la hay: no es la altura del R sobre la línea isoeléctrica),
- frecuencia cardíaca, del mismo RR que entra al QTc.

El ancho de QRS **no se reporta** (ver `qrs_ms` más abajo).

## La evidencia: QT Database de PhysioNet

Antes de escribir una línea de esto se midió el delineador contra el primer
cardiólogo de la QT Database (`qtdb`, anotador `q1c`): 103 registros, 3542
latidos anotados, con réplica exacta de producción (250 → 500 Hz,
`nk.ecg_clean`, bloques de 300 s, R de `nk.ecg_peaks`). El gate del plan era
|bias| ≤ 20 ms en QRS y ≤ 30 ms en QT, por latido **y** por mediana de registro.

**Con la definición del plan ningún método de NeuroKit 0.2.13 pasa el gate
completo:**

- `dwt` da QRS de +74 a +86 ms (es el QRS de 156 ms que se veía en el ECG
  simulado). Su QT "pasa" en tres de cuatro combinaciones solo porque se
  compensan un R_onset temprano y un T_offset temprano, con SD ~100 ms.
- `cwt` pasa el QRS solo en una derivación, cubriendo el 32 % de los latidos y
  con un IC95 que cruza los 20 ms. Su QT da de +60 a +88 ms.
- `prominence` **pasa el QT en las cuatro combinaciones** (dos derivaciones × R
  de referencia o detectados): bias de −6 a −12 ms, mediana por registro de −3 a
  −9 ms, IC95 por bootstrap entero dentro del gate, cobertura del 89-95 %, ~0,03 s
  por bloque. Es la única medición con evidencia a nivel gate y es la que se usa
  acá.

## La decisión: dato de investigación, sin hallazgos

Lo que prevé el plan cuando nada pasa con su definición: las mediciones salen
marcadas `experimental=True` y no hay hallazgos de intervalos (`qrs_wide`,
`qtc_long`, `qtc_short` no existen en el motor). Y más que eso: **el QTc no se
le muestra al médico como un número por paciente**. El pipeline mide cada
bloque analizado (`pipeline.analyze_batch`, detrás de
`ml_interval_measurements_enabled`) y `ml_persistence` guarda una fila por
bloque en `ecg_interval_measurement`, que ninguna API, ni el informe, ni el
visor leen: se exporta para la tesis con
`app.scripts.export_interval_measurements`. Que el bias pase no dice que la
medición siga al paciente:

- el QT de `prominence` correlaciona con el manual entre registros (r = 0,71 a
  0,80), pero esa correlación es casi toda frecuencia cardíaca: el QT manual
  contra el RR ya da r = 0,68 a 0,69;
- el QTc, que es lo que se leería, correlaciona r = 0,24 a 0,30 por registro
  (pendiente 0,17 a 0,20) y r = 0,27 a 0,50 con la mediana de bloque (pendiente
  0,25 a 0,34): un QTc que el cardiólogo marca 100 ms más largo sale 17 a 34 ms
  más largo. Sin las guardas de abajo da r = 0,39 a 0,58: las guardas no miden
  peor, sacan los registros de QT largo, que son los que dan rango;
- el error absoluto del QTc por registro tiene un p90 de 56 a 71 ms.

## Lo que esto no puede medir: el QT largo

Es la razón de fondo de la decisión, y ninguna guarda la arregla.

**El T_offset queda a ≤ 100 ms del pico de la T.** `prominence` lo busca con
`scipy.signal.peak_prominences(wlen = max_t_basepoint_interval = 200 ms)`, que
no mira más allá de ±100 ms del pico. En QTDB el 57-62 % de los latidos válidos
tiene el T_offset exactamente en T_peak + 100 ms: el "QT" es en la práctica
R_onset → T_peak + 100 ms. Con una T normal funciona (pico → fin de T manual:
mediana 92 ms), pero una T ancha termina más lejos (132-136 ms con QTc manual
> 500 ms). En los registros con QTc manual ≥ 470 ms la mediana de bloque sale
25 a 26 ms corta y solo 4 o 5 de los 8 a 10 que reportan bloque salen ≥ 470.
Sobre ECG sintético, un QTc de 570 ms a 60 lpm sale 518 con todos los latidos
válidos (lo fija `test_ml_intervals`). La fracción de latidos topeados no sirve
de aviso: es casi la misma con QT normal y largo (0,54-0,60 contra 0,56-0,66) y
no correlaciona con el error (r ≈ 0,1), así que no se expone.

**La T que no entra en el segmento.** `prominence` busca la T solo hasta la
mitad del R-R siguiente. Con un QT largo eso pasa ya a 70-80 lpm: el T_offset
queda en el borde y el QT sale corto (sintético: QTc real 500, se veía 451 a
80 lpm con cobertura completa). Si el pico de la T cae más allá del borde,
`prominence` elige otro extremo y da un QT de ~240 ms. Las guardas de abajo
(T cortada, techo de FC, cobertura mínima) convierten esos casos en `None`, que
es lo correcto, pero no recuperan la medición.

## Las guardas por latido

Cada una es geometría del delineador o práctica clínica, no un ajuste contra
QTDB; QTDB solo se usó para verificar que no rompen el gate (ver
`IntervalThresholds` para el detalle de cada una):

- **Delineación alineada por latido.** `nk.ecg_delineate` (la API pública) hace
  `[x for x in values if x > 0 or isnan(x)]` sobre cada onda: un valor ≤ 0 se
  *descarta* en vez de volverse NaN y corre un lugar todos los latidos
  siguientes. En el benchmark pasó en 17 corridas. Acá se llama al mismo
  delineador interno y el array queda 1 a 1 con el tren de R (ver
  `_delineate_aligned`), igual que en el harness.
- **Orden fisiológico**: R_onset ≤ R < T_offset < R siguiente.
- **Plausibilidad del QT** (200-650 ms): un latido que el detector perdió deja
  un QT > 1 s aunque el T_offset caiga antes del R siguiente *del tren*.
- **RR**: rango absoluto, techo relativo (hueco del detector), piso relativo
  (prematuridad: saca al prematuro y al anterior) y techo de FC de 100 lpm
  (por encima, una T normal ya no entra en el segmento).
- **El R sobre el pico del QRS**: los R que interpolaba `correct_artifacts`
  (hoy apagada en `detect_rpeaks`) caían entre dos QRS y pasaban todo lo demás.
  La amplitud se mide en el pico.
- **La T entera en el segmento** y **QRS positivo** (con un QRS negativo
  R_onset cae en el valle del QRS y el QT sale corto).
- **Morfología dominante**, cuando el llamador pasa `dominant`: el QT se mide en
  latidos sinusales con vecinos sinusales.
- **Cobertura mínima**: si las guardas sacan a más de la mitad de los
  candidatos, la mediana de los que quedan no representa el bloque.
- **Ningún error de NeuroKit tumba el bloque.** `dwt` revienta con `IndexError` a
  frecuencia muy baja y `cwt` con dos R a < 90 ms o un R pegado al borde;
  `prominence` revienta con un R en la muestra 0 o dos R en muestras contiguas
  (`argmax` de una ventana vacía). Esos dos casos se sanean antes de delinear y
  cualquier otra excepción devuelve `None` para el bloque.

En QTDB cada guarda saca latidos peor medidos que los que deja (MAE de 48-61 ms
los de RR, 41-46 los de QRS negativo, 36-47 los de T cortada, contra 32-34 de
los que quedan; bias de −37 ms los de R fuera del pico) y el resultado sigue
pasando el gate de QT en las cuatro combinaciones, con IC: bias por latido de
−2 a −4 ms, mediana de bloque de +1,2 / +1,3 ms con MAE de 23,6 / 28,2 ms
(derivación 0 / 1; sin estas guardas, −5,5 / −6,4 ms con MAE de 32,6 / 33,1).
El precio es la cobertura: el bloque sale en 64 y 59 de los 103 registros (sin
ellas, 101 y 99), y el 54-64 % de los latidos anotados queda válido. Para un
dato de investigación es el lado correcto, pero **un bloque `None` no es un QT
normal**: las guardas sacan justo los registros de QT largo (con bloque quedan
8 y 10 de los 18 a 21). El costo de cada guarda está en
`tools/physionet/README.md`.

Paridad con el harness (`tools/physionet/qtdb.py`, método `production`, que
corre este módulo tal cual): por latido su QT es idéntico al de `prominence`
donde los dos lo aceptan, todo latido que acepta lo acepta `prominence` con el
filtro `plaus`, y cada latido que `plaus` acepta y este módulo no se explica por
alguna de las máscaras que expone `BeatIntervals`. Con las guardas nuevas
abiertas reproduce exactamente los números anteriores.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

from app.ml.contracts import Indices, Mask, Signal

FloatArray = NDArray[np.float64]

IntervalMethod = Literal["prominence"]
METHOD: Final[IntervalMethod] = "prominence"

#: El delineador vive en un módulo cuyo nombre `neurokit2.ecg` tapa con la
#: función homónima (`import neurokit2.ecg.ecg_delineate as m` devuelve la
#: función), así que se carga por `importlib`. Es una función privada de
#: NeuroKit: `test_ml_intervals` verifica que siga existiendo y que devuelva las
#: mismas marcas sobre una señal fija, para que una actualización de la
#: dependencia rompa CI y no mueva ni apague las mediciones en silencio.
_NK_DELINEATE_MODULE: Final = "neurokit2.ecg.ecg_delineate"
_NK_PROMINENCE: Final = "_prominence_ecg_delineator"
_R_ONSETS: Final = "ECG_R_Onsets"
_T_PEAKS: Final = "ECG_T_Peaks"
_T_OFFSETS: Final = "ECG_T_Offsets"

#: `prominence` revienta con dos R a menos de dos muestras: el segmento del
#: segundo arranca en su propio R y la búsqueda de la Q hace `argmax` de una
#: ventana vacía. Dos R en muestras contiguas son la misma detección.
_MIN_RPEAK_SEPARATION_SAMPLES: Final = 2

#: Antes de buscar R_onset, `prominence` corre el R al máximo de la señal en
#: [R − 20 ms, R + 20 ms) (`_correct_peak`). Acá se repite para saber dónde
#: quedó: la amplitud se mide en ese pico, el mismo del que sale R_onset.
_PEAK_WINDOW_S: Final = 0.02

# Por qué `qrs_ms` es siempre None. En la QT Database el QRS de `prominence`
# (R_onset → R_offset) falla el gate en las cuatro combinaciones (−28 a −29 ms) y
# falla **por construcción**: `scipy.signal.peak_prominences(wlen =
# max_r_basepoint_interval = 100 ms)` deja R_onset y R_offset a ≤ 50 ms del R,
# así que el QRS nunca pasa de 100 ms (máximo exacto 100,0 ms; ninguno de los
# latidos con QRS manual ≥ 120 ms se estimó ≥ 120). Un QRS que no puede ser ancho
# no puede marcar un QRS ancho, y mostrarlo, aun como experimental, lo haría
# parecer normal siempre. Las alternativas tampoco sirven: `dwt` da +75 ms y
# `cwt` cubre ~32 % de los latidos. Las variantes que pasan el bias
# (`max_r_basepoint_interval=200`, Q→S) se encontraron mirando QTDB, no siguen
# el ancho entre registros (r ≤ 0,34) y fallan en MLII: hay que justificarlas y
# validarlas con capturas del chaleco antes de mostrar un ancho de QRS.


@dataclass(frozen=True, slots=True)
class IntervalThresholds:
    """Umbrales de la medición.

    Los de QT salen del benchmark de QTDB; los de RR, de cómo falla un detector
    de R en un Holter (latidos perdidos, dobles detecciones, pausas) y de qué
    latidos se excluyen en la práctica clínica (prematuros y sus vecinos); los
    de la T, de la geometría del delineador. Ninguno se eligió mirando el error
    contra QTDB: QTDB solo se usó para verificar que no rompen el gate.
    """

    #: Latidos válidos mínimos para reportar el bloque (`--min-block-beats` en
    #: el harness de QTDB). Con menos, la mediana la mueve un solo latido mal
    #: delineado.
    min_beats: int = 30
    #: Plausibilidad del QT, en ms. Es el filtro `plaus` del benchmark. El techo
    #: es el que saca de la mediana el QT > 1 s de un latido perdido por el
    #: detector. Es un compromiso: también saca QT reales de bradicardia (QTDB
    #: sel33 y sele0116, QT manual de 764 y 726 ms a RR ~1,65 s, quedan sin
    #: bloque), pero subirlo a 800 deja entrar fines de T mal ubicados (sel14172
    #: L0: 680 ms contra 410) y sube el MAE de bloque. Cuando muerde,
    #: `coverage_ratio` baja.
    qt_min_ms: float = 200.0
    qt_max_ms: float = 650.0
    #: RR plausible, en segundos: 200 a 30 lpm. Un RR fuera de eso es una doble
    #: detección o una pausa, y Fridericia sobre ese RR no corrige nada.
    rr_min_s: float = 0.3
    rr_max_s: float = 2.0
    #: Techo del RR relativo a la mediana del bloque. El detector que pierde un
    #: latido deja un RR de ~2× la mediana a los dos lados del hueco: el latido
    #: siguiente tiene un RR previo falso (QTc falso) y el anterior delinea sobre
    #: un segmento que llega hasta el QRS perdido (QT falso). Es el mismo 1,5×
    #: con el que el benchmark reconocía huecos del detector.
    rr_max_ratio: float = 1.5
    #: Piso del RR relativo a la mediana del bloque, sobre el RR previo **y** el
    #: siguiente. Es el criterio de prematuridad de siempre (un latido que llega
    #: antes del 80 % del ciclo): saca al prematuro (RR previo corto) y al
    #: latido anterior a él, cuya T `prominence` busca solo hasta la mitad de un
    #: RR corto y sale cortada. En bigeminismo no queda ningún latido válido y
    #: el bloque sale None en vez del QTc de los ectópicos. El post-ectópico, con
    #: una pausa compensadora de menos de `rr_max_ratio`, lo saca la máscara
    #: `dominant` cuando el llamador la pasa.
    rr_min_ratio: float = 0.8
    #: Techo de frecuencia, sobre el RR previo y el siguiente. `prominence` busca
    #: la T solo hasta R + RR/2: a 100 lpm eso es R + 300 ms, y un QT normal
    #: (QTc de Fridericia ~400-420 ms, R_onset ~40 ms antes del R) termina justo
    #: ahí. Por encima, cualquier T normal queda cortada y el QTc sale corto en
    #: zona de `qtc_short` (sobre ECG sintético con QTc de 400 ms: 360 a 120 lpm,
    #: 332 a 140). Es geometría del delineador, no un ajuste.
    max_heart_rate_bpm: float = 100.0
    #: El R del tren tiene que estar sobre el pico del QRS: a no más de esto del
    #: máximo de la señal limpia en ±20 ms (el `_correct_peak` de `prominence`).
    #: `detect_rpeaks` usaba `correct_artifacts=True`, que **mueve e inserta** R
    #: por interpolación del ritmo; con un ritmo irregular quedaban R entre dos
    #: QRS que pasaban todos los demás controles (con bigeminismo, 227 de 364).
    #: Hoy detecta sin esa corrección, y la guarda queda para cualquier R que
    #: no caiga sobre el pico.
    peak_tolerance_ms: float = 4.0
    #: Descartar los latidos cuya T no entró en el segmento que mira `prominence`
    #: (R + RR_siguiente/2): el T_offset quedó en la última muestra o el pico de
    #: la T quedó a menos de `t_peak_margin_ms` del final. El QT de esos latidos
    #: es una cota inferior, no una medición.
    reject_truncated_t: bool = True
    t_peak_margin_ms: float = 40.0
    #: Descartar los latidos cuyo R_onset cae sobre una onda negativa más honda
    #: que alto es el R (respecto de la mediana del bloque, que en una señal sin
    #: línea de base es el nivel isoeléctrico). Es un QRS de polaridad negativa:
    #: el "R" que encontró el detector es una S o una J y `prominence` pone
    #: R_onset en el valle del QRS, no en su inicio. Sobre ECG sintético
    #: invertido el QT sale 38 ms corto y la "amplitud R" mide S − R. La
    #: polaridad depende de la colocación del chaleco y del eje del paciente.
    reject_negative_qrs: bool = True
    #: Fracción mínima de los candidatos que tiene que quedar válida para
    #: reportar el bloque. Cuando las guardas sacan a la mayoría de los latidos
    #: sobre señal buena, el delineador no está viendo la T de este paciente (una
    #: T tardía que no entra en el segmento) o el ritmo no es sinusal, y la
    #: mediana de los que quedan es la de un subconjunto elegido por las
    #: guardas: con trigeminismo, la de los post-ectópicos; con una T muy tardía,
    #: un QTc de ~280 ms.
    min_coverage_ratio: float = 0.5

    def __post_init__(self) -> None:
        if self.min_beats < 1:
            raise ValueError("min_beats tiene que ser >= 1")
        if not 0.0 < self.qt_min_ms < self.qt_max_ms:
            raise ValueError("se espera 0 < qt_min_ms < qt_max_ms")
        if not 0.0 < self.rr_min_s < self.rr_max_s:
            raise ValueError("se espera 0 < rr_min_s < rr_max_s")
        if self.rr_max_ratio <= 1.0:
            raise ValueError("rr_max_ratio tiene que ser > 1")
        if not 0.0 <= self.rr_min_ratio < 1.0:
            raise ValueError("se espera 0 <= rr_min_ratio < 1")
        if not 60.0 / self.rr_max_s < self.max_heart_rate_bpm:
            raise ValueError("max_heart_rate_bpm tiene que superar 60 / rr_max_s")
        if self.peak_tolerance_ms < 0.0 or self.t_peak_margin_ms < 0.0:
            raise ValueError("peak_tolerance_ms y t_peak_margin_ms no pueden ser negativos")
        if not 0.0 <= self.min_coverage_ratio <= 1.0:
            raise ValueError("se espera 0 <= min_coverage_ratio <= 1")


@dataclass(frozen=True, slots=True)
class IntervalMeasurement:
    """Medianas de un bloque sobre sus latidos válidos.

    **Dato de investigación, no un número para mostrarle al médico por
    paciente** (`experimental`): ver "Lo que esto no puede medir" en el
    docstring del módulo. Un QT largo sale corto o normal, con cobertura
    completa, y ningún campo de acá lo avisa.
    """

    #: Latidos válidos: los que entran en las medianas.
    beats: int
    #: Latidos medibles en principio: R-R previo entero en señal GOOD; con
    #: `dominant`, latido y vecinos de la morfología dominante; con `owned`,
    #: solo los del llamador.
    candidate_beats: int
    #: `beats / candidate_beats`. Baja cuando el delineador falla sobre señal
    #: buena, cuando la T no entra en el segmento o cuando hay ectopia.
    coverage_ratio: float
    qt_ms: float
    qtc_ms: float
    #: Pico R menos R_onset (el nadir de la Q cuando la hay, no la línea
    #: isoeléctrica), medida sobre `raw_for_amplitude` y no sobre la señal
    #: limpia (ver `measure_intervals`).
    r_amplitude_mv: float
    #: FC de los latidos **medidos** (60 / mediana de su RR previo): es la del
    #: RR que entra al QTc. Las guardas sacan latidos selectivamente, así que
    #: puede apartarse de la del bloque.
    heart_rate_bpm: float
    #: FC de todos los candidatos (sin `dominant`, la del bloque). Si difiere
    #: mucho de `heart_rate_bpm`, los latidos medidos no representan el ritmo
    #: del tramo.
    candidate_heart_rate_bpm: float
    #: Siempre None por ahora: ver el comentario de módulo sobre el techo de
    #: 100 ms de `prominence`.
    qrs_ms: float | None = None
    method: IntervalMethod = METHOD
    experimental: bool = True


@dataclass(frozen=True, slots=True)
class BeatIntervals:
    """El detalle por latido, alineado 1 a 1 con `rpeaks` (el tren saneado).

    Lo que no se pudo medir es NaN. `valid` es la conjunción de todos los
    controles, que también se exponen por separado para poder auditar por qué
    sale cada latido; las medianas del bloque salen solo de `valid`.
    """

    rpeaks: Indices
    #: El R corrido al máximo de la señal limpia en ±20 ms, como hace
    #: `prominence` antes de buscar R_onset. La amplitud se mide acá.
    r_peaks: Indices
    #: R previo en el tren, R-R previo entero en señal GOOD, `dominant_ok` y,
    #: si el llamador pasó `owned`, latido suyo.
    candidate: Mask
    #: R_onset ≤ R < T_offset < R siguiente, con todo el latido en señal GOOD.
    ordered: Mask
    #: QT dentro de [`qt_min_ms`, `qt_max_ms`].
    qt_ok: Mask
    #: RR previo y siguiente dentro de [`rr_floor_s`, `rr_cap_s`].
    rr_ok: Mask
    #: El R del tren está sobre el pico del QRS (`peak_tolerance_ms`).
    peak_ok: Mask
    #: La T entró en el segmento del delineador (siempre verdadero con
    #: `reject_truncated_t=False`).
    t_contained: Mask
    #: R_onset no cae sobre una onda negativa más honda que alto es el R
    #: (siempre verdadero con `reject_negative_qrs=False`).
    qrs_positive: Mask
    #: El latido, el anterior y el siguiente son de la morfología dominante
    #: (siempre verdadero si no se pasó `dominant`). Ya está dentro de
    #: `candidate`.
    dominant_ok: Mask
    valid: Mask
    rr_prev_s: FloatArray
    qt_ms: FloatArray
    qtc_ms: FloatArray
    r_amplitude_mv: FloatArray
    #: Los límites de RR efectivos del bloque, en segundos.
    rr_floor_s: float
    rr_cap_s: float


def fridericia_ms(qt_ms: FloatArray, rr_seconds: FloatArray) -> FloatArray:
    """QTc de Fridericia: QT / RR^(1/3), con el QT en ms y el RR en segundos.

    Fridericia y no Bazett (QT / √RR): Bazett sobrecorrige a frecuencia alta y
    subcorrige a frecuencia baja, y un Holter recorre las dos en el mismo día.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(qt_ms, dtype=np.float64) / np.cbrt(np.asarray(rr_seconds, np.float64))


def measure_intervals(
    cleaned: Signal,
    raw_for_amplitude: Signal,
    rpeaks: Indices,
    good_mask: Mask,
    sample_rate: int,
    thresholds: IntervalThresholds,
    *,
    dominant: Mask | None = None,
    owned: Mask | None = None,
) -> IntervalMeasurement | None:
    """Medianas de QT, QTc, amplitud R y FC de un bloque, o None.

    - `cleaned`: la señal de `clean_signal`, sobre la que se delinea (es la que
      se validó en QTDB).
    - `raw_for_amplitude`: la misma señal con la línea de base y la red quitadas
      pero **sin pasabajos**. La limpieza de producción aplica una media móvil
      de 10 muestras ida y vuelta (un pasabajos de ~16 Hz a 500 Hz) que aplana el
      R: en QTDB la amplitud medida sobre la señal limpia es ~19 % menor que
      sobre la cruda (mediana estimada/referencia 0,81); sobre el pasaaltos de
      0,5 Hz más el notch de `quality.remove_mains`, 1,01-1,02. R_onset queda a
      ≤ 50 ms del R, así que la deriva de línea de base casi no entra en la
      resta; la red sí, y por eso hay que quitarla. `nk.ecg_clean` es de fase cero
      (`sosfiltfilt` y `filtfilt`), así que los índices de `cleaned` valen
      sobre `raw_for_amplitude` siempre que su corrección también lo sea.
    - `rpeaks`: el tren de R del bloque, **completo**, también fuera de las
      ventanas GOOD: el delineador segmenta cada latido hasta la mitad del R-R
      con sus vecinos. Cada R tiene que estar sobre el pico del QRS; los que no
      (los que interpolaba `correct_artifacts`, hoy apagada) se descartan.
    - `good_mask`: verdadero en las muestras de ventanas GOOD.
    - `dominant`: opcional, alineado 1 a 1 con `rpeaks`: verdadero en los
      latidos de la morfología dominante (`morphology.dominant_template`). Con
      ella solo se miden latidos dominantes con vecinos dominantes, que es la
      práctica clínica: el QT se mide en latidos sinusales, sin el ectópico, el
      pre- ni el post-ectópico. Un latido que no se pudo clasificar lo decide el
      llamador (falso lo excluye a él y a sus vecinos).
    - `owned`: opcional, alineado 1 a 1 con `rpeaks`: los latidos que **este**
      llamador informa. Es lo que usa el pipeline, que recibe el bloque con
      contexto a los dos lados: la señal y el tren son los del bloque entero
      —el R previo del primer latido nuevo es del contexto izquierdo, y la T
      del último puede terminar en el derecho—, pero solo se miden los latidos
      con el R en la parte nueva, así que cada latido del estudio entra en la
      mediana de un solo bloque. A diferencia de `dominant`, no se contagia a
      los vecinos: un latido del contexto sigue segmentando y aportando su RR.

    None cuando hay menos de `thresholds.min_beats` latidos válidos o cuando
    NeuroKit falla. Las longitudes distintas entre señales, máscara,
    `dominant` y `owned` son un error del llamador y levantan `ValueError`.
    """
    beats = measure_beats(
        cleaned,
        raw_for_amplitude,
        rpeaks,
        good_mask,
        sample_rate,
        thresholds,
        dominant=dominant,
        owned=owned,
    )
    if beats is None:
        return None
    valid = beats.valid
    n_valid = int(valid.sum())
    n_candidates = int(beats.candidate.sum())
    if n_valid < thresholds.min_beats or n_valid < thresholds.min_coverage_ratio * n_candidates:
        return None
    median_rr = float(np.median(beats.rr_prev_s[valid]))
    candidate_rr = float(np.median(beats.rr_prev_s[beats.candidate]))
    return IntervalMeasurement(
        beats=n_valid,
        candidate_beats=n_candidates,
        coverage_ratio=n_valid / n_candidates,
        qt_ms=float(np.median(beats.qt_ms[valid])),
        qtc_ms=float(np.median(beats.qtc_ms[valid])),
        r_amplitude_mv=float(np.median(beats.r_amplitude_mv[valid])),
        heart_rate_bpm=60.0 / median_rr,
        candidate_heart_rate_bpm=60.0 / candidate_rr,
    )


def measure_beats(
    cleaned: Signal,
    raw_for_amplitude: Signal,
    rpeaks: Indices,
    good_mask: Mask,
    sample_rate: int,
    thresholds: IntervalThresholds,
    *,
    dominant: Mask | None = None,
    owned: Mask | None = None,
) -> BeatIntervals | None:
    """Delineación y controles por latido; mismos argumentos que `measure_intervals`.

    None si no hay ni `min_beats` candidatos (no se gasta la delineación) o si
    NeuroKit falla.
    """
    n = int(cleaned.size)
    if raw_for_amplitude.size != n or good_mask.size != n:
        raise ValueError(
            f"cleaned ({n}), raw_for_amplitude ({raw_for_amplitude.size}) y good_mask "
            f"({good_mask.size}) tienen que tener el mismo largo"
        )
    for name, mask in (("dominant", dominant), ("owned", owned)):
        if mask is not None and np.asarray(mask).size != np.asarray(rpeaks).size:
            raise ValueError(
                f"{name} ({np.asarray(mask).size}) tiene que estar alineado con rpeaks "
                f"({np.asarray(rpeaks).size})"
            )
    if sample_rate <= 0:
        raise ValueError("sample_rate tiene que ser positivo")
    fs = float(sample_rate)

    raw = np.asarray(raw_for_amplitude, dtype=np.float64)
    usable = np.asarray(good_mask, dtype=bool) & np.isfinite(cleaned) & np.isfinite(raw)
    ecg = np.nan_to_num(np.asarray(cleaned, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    train, kept = _sanitize_rpeaks(rpeaks, n)
    m = int(train.size)
    if m < 3:
        return None

    # Suma acumulada de la máscara: "todo el tramo es GOOD" para todos los
    # latidos a la vez, igual que `hrv.build_rr`.
    cumulative = np.concatenate(([0], np.cumsum(usable, dtype=np.int64)))
    rr_prev = np.full(m, np.nan)
    rr_prev[1:] = np.diff(train) / fs
    rr_next = np.full(m, np.nan)
    rr_next[:-1] = rr_prev[1:]

    # Candidato: tiene R previo, el R-R previo cae entero en señal GOOD (un R-R
    # que cruza un tramo malo no mide el ritmo: mide que faltan latidos) y, con
    # `dominant`, el latido y sus vecinos son de la morfología dominante. Un
    # ectópico no es un latido que el delineador no pudo medir: no se mide, y
    # por eso no cuenta en `coverage_ratio` ni en la mediana de RR del bloque.
    # Lo mismo un latido que no es del llamador (`owned`): lo informa otro
    # bloque.
    dominant_ok = _dominant_neighbourhood(dominant, kept, m)
    candidate = np.zeros(m, dtype=bool)
    candidate[1:] = _span_usable(cumulative, train[:-1], train[1:])
    candidate &= dominant_ok
    if owned is not None:
        candidate &= np.asarray(owned, dtype=bool).ravel()[kept]
    if int(candidate.sum()) < thresholds.min_beats:
        return None

    try:
        waves = _delineate_aligned(ecg, train, sample_rate)
    except Exception:  # noqa: BLE001 — cualquier degeneración del delineador
        return None
    r_on = waves[_R_ONSETS]
    t_peak = waves[_T_PEAKS]
    t_off = waves[_T_OFFSETS]

    r = train.astype(np.float64)
    r_next = np.full(m, np.inf)
    r_next[:-1] = r[1:]
    qt_ms = (t_off - r_on) * 1000.0 / fs
    qtc_ms = fridericia_ms(qt_ms, rr_prev)

    # Orden fisiológico: R_onset ≤ R < T_offset < R siguiente, con todo el
    # latido en señal GOOD. NaN no pasa ninguna comparación, así que también
    # filtra lo no delineado.
    ordered = (r_on <= r) & (r < t_off) & (t_off < r_next)
    ordered_idx = np.flatnonzero(ordered)
    on_idx = r_on[ordered_idx].astype(np.int64)
    off_idx = t_off[ordered_idx].astype(np.int64)
    ordered[ordered_idx] = _span_usable(cumulative, on_idx, off_idx)

    # La amplitud se mide en el pico al que `prominence` corrió el R, que es de
    # donde sale R_onset; el R del tren tiene que estar ahí mismo.
    peaks = _corrected_peaks(ecg, train, sample_rate)
    tolerance = int(round(thresholds.peak_tolerance_ms * fs / 1000.0))
    peak_ok = np.abs(peaks - train) <= tolerance
    amplitude = np.full(m, np.nan)
    amplitude[ordered_idx] = raw[peaks[ordered_idx]] - raw[on_idx]
    qrs_positive = np.ones(m, dtype=bool)
    if thresholds.reject_negative_qrs:
        # Sin línea de base, la mediana del bloque es el nivel isoeléctrico: la
        # mayor parte de un ciclo es segmento TP y PR.
        level = float(np.median(ecg[usable]))
        rise = ecg[peaks[ordered_idx]] - level
        dip = level - ecg[on_idx]
        qrs_positive[ordered_idx] = rise >= dip

    median_rr = float(np.median(rr_prev[candidate]))
    rr_floor = max(
        thresholds.rr_min_s,
        60.0 / thresholds.max_heart_rate_bpm,
        thresholds.rr_min_ratio * median_rr,
    )
    rr_cap = min(thresholds.rr_max_s, thresholds.rr_max_ratio * median_rr)
    rr_prev_ok = (rr_prev >= rr_floor) & (rr_prev <= rr_cap)
    # El último latido del bloque no tiene R siguiente: se le cree, igual que en
    # el benchmark (T_offset < +inf).
    rr_next_ok = np.isnan(rr_next) | ((rr_next >= rr_floor) & (rr_next <= rr_cap))
    rr_ok = rr_prev_ok & rr_next_ok
    qt_ok = (qt_ms >= thresholds.qt_min_ms) & (qt_ms <= thresholds.qt_max_ms)
    t_contained = np.ones(m, dtype=bool)
    if thresholds.reject_truncated_t:
        margin = int(round(thresholds.t_peak_margin_ms * fs / 1000.0))
        t_contained = ~_t_truncated(train, t_peak, t_off, n, margin)

    valid = (
        candidate
        & ordered
        & qt_ok
        & rr_ok
        & peak_ok
        & t_contained
        & qrs_positive
        & np.isfinite(qtc_ms)
        & np.isfinite(amplitude)
    )
    return BeatIntervals(
        rpeaks=train,
        r_peaks=peaks,
        candidate=candidate,
        ordered=ordered,
        qt_ok=qt_ok,
        rr_ok=rr_ok,
        peak_ok=peak_ok,
        t_contained=t_contained,
        qrs_positive=qrs_positive,
        dominant_ok=dominant_ok,
        valid=valid,
        rr_prev_s=rr_prev,
        qt_ms=qt_ms,
        qtc_ms=qtc_ms,
        r_amplitude_mv=amplitude,
        rr_floor_s=rr_floor,
        rr_cap_s=rr_cap,
    )


def _sanitize_rpeaks(rpeaks: Indices, n: int) -> tuple[Indices, Indices]:
    """Tren ordenado, sin repetidos, dentro de [1, n − 1] y sin R contiguos.

    Devuelve el tren y, por cada R que quedó, su posición en `rpeaks` (para
    llevar `dominant` al tren saneado). La muestra 0 se descarta porque
    `prominence` revienta con un R ahí (la ventana de la Q queda vacía). No se
    toca nada más del tren: un R espurio a 40 ms de otro se deja, y los
    controles de RR sacan a los dos latidos afectados sin alterar la
    segmentación de sus vecinos.
    """
    peaks, first = np.unique(np.asarray(rpeaks, dtype=np.int64).ravel(), return_index=True)
    inside = (peaks >= 1) & (peaks <= n - 1)
    peaks, first = peaks[inside], first[inside].astype(np.int64)
    if peaks.size < 2 or not (np.diff(peaks) < _MIN_RPEAK_SEPARATION_SAMPLES).any():
        return peaks, first
    kept = [0]
    for k in range(1, peaks.size):
        if int(peaks[k]) - int(peaks[kept[-1]]) >= _MIN_RPEAK_SEPARATION_SAMPLES:
            kept.append(k)
    keep = np.asarray(kept, dtype=np.int64)
    return peaks[keep], first[keep]


def _dominant_neighbourhood(dominant: Mask | None, kept: Indices, m: int) -> Mask:
    """Verdadero donde el latido, el anterior y el siguiente son dominantes.

    Sin `dominant`, todo verdadero. El primer latido no tiene anterior (ya lo
    saca `candidate`) y al último se le cree el siguiente, como con el RR.
    """
    if dominant is None:
        return np.ones(m, dtype=bool)
    dom = np.asarray(dominant, dtype=bool).ravel()[kept]
    result = dom.copy()
    result[1:] &= dom[:-1]
    result[:-1] &= dom[1:]
    return result


def _corrected_peaks(ecg: FloatArray, train: Indices, sample_rate: int) -> Indices:
    """El R de cada latido corrido al máximo de `ecg` en [R − 20 ms, R + 20 ms).

    Es el `_correct_peak` que `prominence` aplica antes de buscar R_onset (la
    misma ventana, el mismo `argmax`), vectorizado.
    """
    half = int(_PEAK_WINDOW_S * sample_rate)
    if half < 1:
        return train.copy()
    padded = np.concatenate((np.full(half, -np.inf), ecg, np.full(half, -np.inf)))
    # La ventana k de `padded` es ecg[k − half : k + half].
    windows = sliding_window_view(padded, 2 * half)
    corrected: Indices = train - half + np.argmax(windows[train], axis=1)
    return corrected


def _segment_last(train: Indices, n: int) -> Indices:
    """Última muestra del segmento en el que `prominence` busca la T de cada latido.

    Termina en R + RR_siguiente // 2 (el último latido usa el R-R anterior,
    como hace NeuroKit) y nunca pasa del final de la señal.
    """
    half_next = np.empty(train.size, dtype=np.int64)
    half_next[:-1] = np.diff(train) // 2
    half_next[-1] = (train[-1] - train[-2]) // 2
    last: Indices = np.minimum(train + half_next - 1, n - 1)
    return last


def _t_truncated(
    train: Indices, t_peak: FloatArray, t_off: FloatArray, n: int, margin: int
) -> Mask:
    """Verdadero donde la T no entró en el segmento del latido.

    El T_offset cayó en la última muestra del segmento, o el pico de la T quedó
    a menos de `margin` muestras del final (la bajada de la T no tiene lugar).
    NaN da falso: lo no delineado ya lo saca otro control.
    """
    last = _segment_last(train, n).astype(np.float64)
    truncated: Mask = (t_off >= last) | (t_peak > last - margin)
    return truncated


def _span_usable(cumulative: NDArray[np.int64], start: Indices, end: Indices) -> Mask:
    """Verdadero donde todas las muestras de [start, end] (inclusive) son GOOD."""
    good = cumulative[end + 1] - cumulative[start]
    result: Mask = good == (end - start + 1)
    return result


def _prominence_delineator() -> Callable[..., Any]:
    module = importlib.import_module(_NK_DELINEATE_MODULE)
    delineator: Callable[..., Any] = getattr(module, _NK_PROMINENCE)
    return delineator


def _delineate_aligned(ecg: FloatArray, rpeaks: Indices, sample_rate: int) -> dict[str, FloatArray]:
    """`ecg_delineate(method="prominence")` con las ondas alineadas 1 a 1 con `rpeaks`.

    Port del `delineate_aligned` del benchmark de QTDB: el mismo delineador
    interno que despacha la API pública, el mismo `>= len → NaN` que ella aplica,
    y los valores ≤ 0 a NaN en lugar de descartarlos (la API pública los saca de
    la lista y corre los latidos siguientes un lugar). Si un array no sale del
    largo del tren se levanta: alinearlo a ciegas sería medir el QT de un latido
    con el R de otro.
    """
    waves = _prominence_delineator()(ecg, rpeaks=rpeaks, sampling_rate=sample_rate)
    out: dict[str, FloatArray] = {}
    for key in (_R_ONSETS, _T_PEAKS, _T_OFFSETS):
        values = np.array(
            [np.nan if value is None else value for value in list(waves[key])],
            dtype=np.float64,
        )
        if values.size != rpeaks.size:
            raise ValueError(f"{key}: {values.size} valores para {rpeaks.size} latidos")
        values[(values <= 0) | (values >= ecg.size)] = np.nan
        out[key] = values
    return out
