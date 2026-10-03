"""Serie R-R y prematuridad.

La serie R-R es la entrada de las dos cosas que vienen después: las reglas de
ritmo (`arrhythmia.py`) y el eje de **prematuridad** del score de anomalía
(`morphology.py`). Un latido ectópico no se define solo por tener otra forma:
llega **antes de tiempo**. Morfología rara sin prematuridad es, casi siempre, un
artefacto de movimiento.

Las métricas de variabilidad en el dominio del tiempo (SDNN, RMSSD, pNN50)
viven acá en dos versiones que tienen que coincidir: `hrv_summary`, directa
sobre una serie, y los acumuladores de `totals.py`, que suman bloques y dan el
valor exacto del estudio entero. Las dos cuentan los mismos intervalos
(`counted_intervals`) y las mismas diferencias (`successive_pairs`). El dominio
frecuencial (requerimiento **Should** de `Requerimientos.md` §6.B) queda fuera
de este alcance.

No se usa `nk.hrv_time`: devuelve un `DataFrame` de pandas —lento, y `Any` para
mypy— por seis fórmulas de una línea.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.ml.contracts import Floats, Indices, Mask

#: Latidos de la mediana móvil que define "el ritmo de este momento". 32 latidos
#: son ~30 s: largo para que una extrasístole aislada no mueva la referencia,
#: corto para seguir un cambio real de frecuencia.
LOCAL_WINDOW_BEATS = 32

#: Prematuridad por debajo de la cual un latido **no es normal** para la
#: variabilidad: llegó con un R-R un 20 % más corto que el ritmo del momento. Es
#: el criterio clásico de edición de la serie NN (excluir lo que se aparta más
#: de un 20 %), aplicado solo hacia el lado prematuro: el intervalo largo que
#: sigue a un ectópico ya sale por tocar al ectópico, y un R-R largo sin
#: ectópico es una pausa o una bradicardia, que es variabilidad real.
ECTOPIC_PREMATURITY = 0.8


@dataclass(frozen=True, slots=True)
class RRSeries:
    """Intervalos entre R consecutivos. `rr_seconds[i]` va de `rpeaks[i]` a `[i+1]`."""

    rpeaks: Indices
    rr_seconds: Floats
    #: Verdadero si el intervalo cae **entero** en señal analizable. Un R-R que
    #: cruza un tramo de electrodo despegado no mide una pausa: mide que faltan
    #: latidos que sí ocurrieron.
    valid: Mask
    #: La de `rpeaks`. Hace falta para medir en milisegundos sin pasar por
    #: `rr_seconds`, que es float32 (ver `nn_milliseconds`).
    sample_rate: int

    @property
    def n_beats(self) -> int:
        return int(self.rpeaks.size)


def build_rr(rpeaks: Indices, analyzable: Mask, sample_rate: int) -> RRSeries:
    if rpeaks.size < 2:
        return RRSeries(
            rpeaks=rpeaks,
            rr_seconds=np.empty(0, dtype=np.float32),
            valid=np.empty(0, dtype=bool),
            sample_rate=sample_rate,
        )
    rr = np.diff(rpeaks).astype(np.float32) / float(sample_rate)

    # Un intervalo es válido si todo el tramo entre los dos R es analizable. Se
    # resuelve con la suma acumulada de la máscara: comparar el conteo de
    # muestras buenas contra el largo del intervalo es O(n) para todos a la vez.
    cumulative = np.concatenate(([0], np.cumsum(analyzable.astype(np.int64))))
    starts = rpeaks[:-1]
    ends = rpeaks[1:]
    good_samples = cumulative[ends] - cumulative[starts]
    valid = good_samples == (ends - starts)
    return RRSeries(rpeaks=rpeaks, rr_seconds=rr, valid=valid.astype(bool), sample_rate=sample_rate)


def local_median_rr(rr_seconds: Floats, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Mediana móvil centrada de la serie R-R, del mismo largo que la entrada.

    Es la referencia contra la que se mide la prematuridad. Tiene que ser
    **local**: la frecuencia cardíaca de un paciente cambia por un factor de dos
    entre dormir y subir una escalera, y una referencia global marcaría toda la
    actividad diurna como prematura.

    Centrada quiere decir que mira `window_beats // 2` latidos **hacia
    adelante**, y en el final de la serie los repite (`mode="edge"`). Un bloque
    del cursor que terminara justo en un ectópico se mediría contra su propio
    R-R corto: prematuridad ~1, y el ectópico entraba en la variabilidad. Por
    eso los bloques se analizan con contexto derecho
    (`settings.ml_analysis_lookahead_seconds`), largo para esos latidos.
    """
    if rr_seconds.size == 0:
        return np.empty(0, dtype=np.float32)
    if rr_seconds.size <= window_beats:
        return np.full(rr_seconds.size, float(np.median(rr_seconds)), dtype=np.float32)

    half = window_beats // 2
    padded = np.pad(rr_seconds.astype(np.float64), (half, window_beats - half - 1), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window_beats)
    return np.asarray(np.median(windows, axis=-1), dtype=np.float32)


def prematurity(rr: RRSeries, window_beats: int = LOCAL_WINDOW_BEATS) -> Floats:
    """Por latido: `RR_precedente / RR_medio_local`. Menor que 1 = prematuro.

    El primer latido no tiene intervalo precedente y queda en 1,0 (neutro): no
    hay evidencia de que sea prematuro, y asumir lo contrario lo marcaría por el
    solo hecho de abrir el lote.
    """
    result = np.ones(rr.n_beats, dtype=np.float32)
    if rr.rr_seconds.size == 0:
        return result
    reference = local_median_rr(rr.rr_seconds, window_beats)
    safe = np.where(reference > 0, reference, np.float32(1.0))
    result[1:] = rr.rr_seconds / safe
    return result


def instantaneous_bpm(rr_seconds: Floats) -> Floats:
    if rr_seconds.size == 0:
        return np.empty(0, dtype=np.float32)
    safe = np.where(rr_seconds > 0, rr_seconds, np.float32(np.inf))
    return np.asarray(60.0 / safe, dtype=np.float32)


def normal_intervals(rr: RRSeries) -> Mask:
    """Por intervalo: verdadero si es **NN** —normal a normal— y no solo R-R.

    SDNN, RMSSD y pNN50 se definen sobre intervalos entre latidos normales. Un
    ectópico deja dos intervalos que no son variabilidad del nodo sinusal: el
    corto que lo trae y la pausa compensadora que lo sigue. Contados, un
    registro a 60 lpm exactos con un ectópico cada doce latidos daba un RMSSD de
    284 ms —un tono vagal de atleta— donde el real es cero. Sale el intervalo
    que toca un latido prematuro (`ECTOPIC_PREMATURITY`) en cualquiera de sus
    dos puntas.
    """
    if rr.rr_seconds.size == 0:
        return np.empty(0, dtype=bool)
    premature = prematurity(rr) < ECTOPIC_PREMATURITY
    return np.asarray(~(premature[:-1] | premature[1:]), dtype=bool)


def counted_intervals(rr: RRSeries, from_sample: int = 0, to_sample: int | None = None) -> Mask:
    """Por intervalo: verdadero si entra en las métricas de variabilidad.

    Un R-R cuenta si es válido, es NN (`normal_intervals`) y su latido
    **final** cae en `[from_sample, to_sample)`. Es la regla que hace sumables
    los bloques del cursor: cada intervalo tiene un solo latido final, ese
    latido cae en la parte nueva de un solo bloque, y así ningún intervalo se
    cuenta dos veces aunque los contextos de los bloques vecinos lo vuelvan a
    ver. El que empieza en el contexto izquierdo y termina en la parte nueva
    cuenta acá; el que termina en el contexto derecho, en el bloque siguiente.
    `to_sample` en `None` es hasta el final de la serie.
    """
    if rr.valid.size == 0:
        return np.empty(0, dtype=bool)
    ends = rr.rpeaks[1:]
    inside = ends >= from_sample
    if to_sample is not None:
        inside &= ends < to_sample
    return np.asarray(rr.valid & normal_intervals(rr) & inside, dtype=bool)


def successive_pairs(rr: RRSeries, counted: Mask) -> Mask:
    """Por intervalo `i`: verdadero si la diferencia `(RR[i-1], RR[i])` cuenta.

    Los dos tienen que ser válidos, NN y **adyacentes** —consecutivos en la
    serie, sin un intervalo descartado entre los dos—, y el segundo tiene que
    contar (`counted_intervals`). El primero puede estar en el contexto: la
    diferencia se atribuye al bloque de su segundo intervalo, por la misma
    razón que el intervalo se atribuye al de su latido final.

    Diferenciar la serie de válidos concatenada, que es lo que hacía
    `hrv_summary` antes, une los dos lados de cada tramo descartado: el último
    R-R antes de un electrodo despegado contra el primero después, minutos más
    tarde, como si fueran latidos seguidos. Eso infla el RMSSD justo en los
    registros con más artefactos.
    """
    pairs = np.zeros(counted.size, dtype=bool)
    if counted.size >= 2:
        pairs[1:] = counted[1:] & (rr.valid & normal_intervals(rr))[:-1]
    return pairs


def nn_milliseconds(rr: RRSeries) -> NDArray[np.float64]:
    """La serie R-R en milisegundos, en float64: la base de todas las sumas.

    Sale de la diferencia **entera** de muestras y no de `rr_seconds`. A 500 Hz
    cada intervalo es un múltiplo exacto de 2 ms, así que una diferencia
    sucesiva de exactamente 50 ms (25 muestras) no es un caso de borde sino un
    valor que aparece seguido. Pasando por float32 queda en 49,99998 o en
    50,00002 según el intervalo, y el pNN50 —que cuenta las que **superan**
    50 ms— se corría hasta medio punto por redondeo.
    """
    if rr.rpeaks.size < 2:
        return np.empty(0, dtype=np.float64)
    return np.diff(rr.rpeaks).astype(np.float64) * 1000.0 / float(rr.sample_rate)


def hrv_summary(rr: RRSeries) -> dict[str, float]:
    """Resumen directo sobre los intervalos NN válidos de **una** serie. Vacío si no hay dos.

    Es la cuenta de referencia, con numpy y sin acumuladores: el estudio se
    resume con `totals.summary_from_totals`, que tiene que dar lo mismo sobre un
    solo bloque sin contexto y es lo que verifica que las sumas estén bien. Las
    diferencias sucesivas usan la adyacencia de `successive_pairs`.
    """
    counted = counted_intervals(rr)
    if int(np.count_nonzero(counted)) < 2:
        return {}
    nn_ms = nn_milliseconds(rr)
    valid = nn_ms[counted]
    pairs = successive_pairs(rr, counted)
    diffs = np.diff(nn_ms)[pairs[1:]]
    bpm = instantaneous_bpm(rr.rr_seconds[counted])
    summary = {
        "beats": float(rr.n_beats),
        "meanNNms": round(float(np.mean(valid)), 3),
        "sdnnMs": round(float(np.std(valid, ddof=1)), 3),
        "meanBpm": round(float(np.mean(bpm)), 3),
        "minBpm": round(float(np.min(bpm)), 3),
        "maxBpm": round(float(np.max(bpm)), 3),
    }
    if diffs.size:
        summary["rmssdMs"] = round(float(np.sqrt(np.mean(diffs**2))), 3)
        summary["pnn50"] = round(float(np.mean(np.abs(diffs) > 50.0)), 6)
    return summary
