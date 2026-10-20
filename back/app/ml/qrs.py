"""Delineación del QRS por latido: inicio, fin, ancho y amplitud.

Lo que el motor necesita saber de cada latido para clasificarlo (V con QRS
ancho, S con QRS angosto) y para informar el ancho y el alto del complejo. No es
el QRS de `intervals.py`: aquél sale del delineador `prominence` de NeuroKit,
que su propio docstring documenta como incapaz de dar más de 100 ms (y `dwt`
sale +75 ms), así que no sirve para decidir si un QRS pasa los 120 ms.

**El método**, vectorizado sobre la matriz de latidos del bloque:

1. Una ventana de ±`QRS_WINDOW_MS` alrededor del R sobre la señal de análisis
   (`pipeline._amplitude_signal`: sin red y sin línea de base, **sin pasabajos**;
   el filtro de 40 Hz de visualización aplana el R y ensancha el QRS).
2. La pendiente absoluta, con la derivada de cinco puntos de Savitzky-Golay
   (±8 ms): el QRS es lo único del latido con pendiente comparable a la del R.
3. El umbral es una fracción de la pendiente máxima del latido
   (`SLOPE_FRACTION`), con un piso por ruido. Las muestras sobre el umbral se
   unen si las separa menos de `GAP_MS`: la pendiente se anula en el pico de la
   R, en el valle de la Q y en el de la S, y eso no corta el complejo.
4. Inicio y fin son los bordes del tramo que contiene al R.

Es lo que se valida contra la QT Database (`tools/physionet/qrs_validation.py`);
el resultado condiciona si el ancho se usa para clasificar
(`QRS_WIDTH_VALIDATED`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy.ndimage import convolve1d

from app.ml.contracts import Floats, Indices, Mask, Signal

#: Mitad de la ventana del latido, en ms. Un QRS ventricular llega a 200 ms, y
#: el extremo tiene que dejar aire para que el tramo termine adentro.
QRS_WINDOW_MS: Final = 160

#: Zona alrededor del R donde se busca la pendiente máxima. El R del detector
#: puede estar corrido unos milisegundos del fiducial del QRS.
CORE_MS: Final = 40

#: Fracción de la pendiente máxima por debajo de la cual la señal ya no está en
#: el QRS. Elegida entre 0,02 y 0,25 contra la QT Database (`q1c`) y MIT-BIH:
#: con un umbral bajo (0,04) el sesgo de ancho en QTDB es el mejor, pero la
#: cola de una T alta con un QRS chico (MIT-BIH 234) entra en el complejo y un
#: normal mide 150 ms; con 0,08 y la extensión de abajo el sesgo queda en
#: pocos ms y esa T queda afuera (`docs/deteccion-ml/README.md`).
SLOPE_FRACTION: Final = 0.08

#: Hueco máximo entre muestras sobre el umbral que no corta el complejo: el
#: cruce por cero de la pendiente en el pico de R, y en los valles de Q y S.
GAP_MS: Final = 16

#: Piso por ruido: múltiplo de la pendiente típica (mediana) fuera del núcleo.
#: Con señal limpia casi no actúa; con ruido evita que el umbral caiga a cero y
#: el tramo se extienda por toda la ventana. Con 1,5 el 20 % de los latidos de
#: QTDB no se delimitaba (el tramo llegaba al borde); con 4 el complejo salía
#: 20 ms corto.
NOISE_FLOOR_FACTOR: Final = 2.5

#: Extensión fija a cada lado del tramo de pendiente. El umbral encuentra la
#: parte empinada del complejo, no el pie donde el cardiólogo marca el inicio
#: y el fin; medido contra QTDB, con 0,08 el tramo sale ~18 ms más corto que la
#: marca manual de ancho (9 por lado), y esta constante lo compensa.
EDGE_EXTENSION_MS: Final = 8

#: Un QRS medido por debajo o por encima de estos anchos no es un QRS: es una
#: ventana sin complejo, o uno pegado al ruido o a la T.
MIN_QRS_MS: Final = 40.0
MAX_QRS_MS: Final = 280.0

#: Amplitud pico a pico mínima del complejo. Por debajo, el R del detector no
#: es un QRS medible (ni siquiera para un electrodo seco).
MIN_AMPLITUDE_MV: Final = 0.05

#: Derivada de cinco puntos: pendiente de la recta de mínimos cuadrados
#: (Savitzky-Golay, orden 1), con la señal en mV y el resultado en mV/muestra.
_DERIVATIVE: Final = np.array([2.0, 1.0, 0.0, -1.0, -2.0]) / 10.0

#: Límite de ancho a partir del cual un QRS es "ancho" (AHA/ACC/HRS 2009,
#: bloqueo de rama completo y complejos ventriculares). Vive acá y no en la
#: clasificación porque lo usan tres módulos.
WIDE_QRS_MS: Final = 120.0

#: Si el ancho medido pasó la validación contra QTDB (sesgo <= 20 ms, ver
#: `tools/physionet/qrs_validation.py` y el README de `docs/deteccion-ml`).
#: En falso, el ancho se persiste igual pero no clasifica ni genera
#: `wide_qrs_sustained`.
QRS_WIDTH_VALIDATED: Final = True


@dataclass(frozen=True, slots=True)
class QrsDelineation:
    """Por R del tren de entrada. Las muestras son **relativas al R**."""

    #: Negativo (antes del R). Sin sentido donde `valid` es falso.
    onset: Indices
    #: Positivo (después del R).
    offset: Indices
    #: Pico a pico entre inicio y fin, en mV. NaN donde `valid` es falso.
    amplitude_mv: Floats
    valid: Mask
    #: Ruido de alta frecuencia de la ventana del latido (desvío del ruido
    #: blanco equivalente, en mV). NaN donde no se midió. Es la guarda de ruido
    #: local de la clasificación: un latido cuyo ruido es una fracción grande de
    #: su propia amplitud no se clasifica (`beat_classification`).
    noise_mv: Floats

    def select(self, keep: Mask) -> QrsDelineation:
        """Las filas de `keep`, en el mismo orden."""
        return QrsDelineation(
            onset=self.onset[keep],
            offset=self.offset[keep],
            amplitude_mv=self.amplitude_mv[keep],
            valid=self.valid[keep],
            noise_mv=self.noise_mv[keep],
        )

    def width_ms(self, sample_rate: int) -> Floats:
        """Duración en ms; NaN donde no se midió."""
        width = (self.offset - self.onset).astype(np.float32) * (1000.0 / sample_rate)
        return np.where(self.valid, width, np.float32(np.nan)).astype(np.float32)


def bridged_extent(above: Mask, centre: int, gap: int) -> tuple[Indices, Indices, Mask]:
    """Alcance del tramo que contiene la columna `centre` en cada fila de `above`.

    Une las muestras separadas por menos de `gap` y devuelve, por fila, a cuántas
    muestras del centro cae la **primera muestra fuera** del tramo (hacia la
    izquierda y hacia la derecha), y si el tramo se pudo delimitar: el centro
    tiene que estar adentro y el tramo no puede llegar al borde de la ventana.
    Los alcances incluyen los `gap` que el puente agrega a cada lado: quien los
    usa los descuenta. Lo comparten el QRS y la onda P (`conduction.py`).
    """
    bridged = convolve1d(above.astype(np.float32), np.ones(2 * gap + 1, dtype=np.float32), axis=1)
    bridged = bridged > 0
    left = ~bridged[:, : centre + 1][:, ::-1]
    right = ~bridged[:, centre:]
    found = left.any(axis=1) & right.any(axis=1) & bridged[:, centre]
    return np.argmax(left, axis=1), np.argmax(right, axis=1), found


def empty_delineation(n: int) -> QrsDelineation:
    return QrsDelineation(
        onset=np.zeros(n, dtype=np.int64),
        offset=np.zeros(n, dtype=np.int64),
        amplitude_mv=np.full(n, np.nan, dtype=np.float32),
        valid=np.zeros(n, dtype=bool),
        noise_mv=np.full(n, np.nan, dtype=np.float32),
    )


def delineate_qrs(
    signal: Signal, rpeaks: Indices, analyzable: Mask, sample_rate: int
) -> QrsDelineation:
    """Inicio, fin y amplitud del QRS de cada R. **Puro y vectorizado.**

    `signal` es la de análisis (sin pasabajos) y `analyzable` marca la señal
    GOOD: un latido con alguna muestra de su ventana fuera de ahí no se mide
    —un artefacto pegado al QRS lo ensancharía— y queda con `valid` en falso.
    """
    n = int(rpeaks.size)
    result = empty_delineation(n)
    half = int(round(QRS_WINDOW_MS * sample_rate / 1000))
    if n == 0 or signal.size < 2 * half + 1:
        return result

    inside = (rpeaks >= half) & (rpeaks + half < signal.size)
    if analyzable.size == signal.size:
        cumulative = np.concatenate(([0], np.cumsum(analyzable.astype(np.int64))))
        low = np.clip(rpeaks - half, 0, signal.size)
        high = np.clip(rpeaks + half + 1, 0, signal.size)
        inside &= (cumulative[high] - cumulative[low]) == (2 * half + 1)
    rows = np.flatnonzero(inside)
    if rows.size == 0:
        return result

    windows = np.lib.stride_tricks.sliding_window_view(signal, 2 * half + 1)[rpeaks[rows] - half]
    slope = np.abs(convolve1d(windows.astype(np.float32), _DERIVATIVE, axis=1, mode="nearest"))

    centre = half
    core = int(round(CORE_MS * sample_rate / 1000))
    peak_slope = slope[:, centre - core : centre + core + 1].max(axis=1)
    outside = np.ones(2 * half + 1, dtype=bool)
    outside[centre - core : centre + core + 1] = False
    typical = np.median(slope[:, outside], axis=1)
    threshold = np.maximum(SLOPE_FRACTION * peak_slope, NOISE_FLOOR_FACTOR * typical)

    gap = max(int(round(GAP_MS * sample_rate / 1000)), 1)
    left_stop, right_stop, has_edges = bridged_extent(slope >= threshold[:, None], centre, gap)
    # El puente agranda el tramo `gap` muestras a cada lado: se descuenta.
    extension = int(round(EDGE_EXTENSION_MS * sample_rate / 1000))
    onset = np.minimum(-(left_stop - 1) + gap, -1) - extension
    offset = np.maximum((right_stop - 1) - gap, 1) + extension

    columns = np.arange(-half, half + 1)
    in_complex = (columns[None, :] >= onset[:, None]) & (columns[None, :] <= offset[:, None])
    high_value = np.where(in_complex, windows, -np.inf).max(axis=1)
    low_value = np.where(in_complex, windows, np.inf).min(axis=1)
    amplitude = (high_value - low_value).astype(np.float32)

    width_ms = (offset - onset) * (1000.0 / sample_rate)
    valid = (
        has_edges
        & (width_ms >= MIN_QRS_MS)
        & (width_ms <= MAX_QRS_MS)
        & np.isfinite(amplitude)
        & (amplitude >= MIN_AMPLITUDE_MV)
    )
    result.onset[rows] = onset
    result.offset[rows] = offset
    result.amplitude_mv[rows] = np.where(valid, amplitude, np.float32(np.nan))
    result.valid[rows] = valid
    # Ruido blanco equivalente: la segunda diferencia anula una recta y deja
    # el ruido (desvío * sqrt(6)); la mediana absoluta la deja en pie aunque el
    # complejo ocupe parte de la ventana.
    second = np.abs(np.diff(windows.astype(np.float32), n=2, axis=1))
    result.noise_mv[rows] = np.median(second, axis=1) * (1.4826 / np.sqrt(6.0))
    return result
