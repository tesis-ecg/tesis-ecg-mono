"""Acumuladores del estudio: lo que se suma bloque a bloque y da métricas exactas.

El estudio no se analiza de una vez: llega en bloques de unos minutos, y el
SDNN de un estudio de quince días **no** es el promedio de los SDNN de sus
bloques. Lo que sí se puede sumar son los momentos: con `n`, `Σx` y `Σx²` de los
intervalos NN la media y el desvío del estudio entero salen exactos, y con `n`
y `Σd²` de las diferencias sucesivas, el RMSSD. Cada bloque devuelve sus sumas
(`block_totals`), la persistencia las acumula con `combine_totals` y el resumen
legible sale de `summary_from_totals` en el momento de leerlo.

Que la suma sea exacta depende de que cada cosa se cuente **una sola vez**. El
cursor de bloques garantiza que cada muestra cae en la parte nueva de un solo
bloque; lo que no es una muestra se atribuye por una regla que también es
única:

- un latido, al bloque cuya parte nueva tiene su R;
- un intervalo NN, al bloque de su latido final (`hrv.counted_intervals`, que
  además deja afuera los que tocan un ectópico);
- una diferencia sucesiva, al bloque de su segundo intervalo
  (`hrv.successive_pairs`);
- una ventana de calidad, al bloque cuya parte nueva la contiene entera
  (`quality.block_window_bounds` pone un borde justo en cada lado de la parte
  nueva).

Única no alcanza: el bloque que cuenta tiene que **ver bien** lo que cuenta.
Sin contexto derecho, el final de cada bloque era un borde duro de señal: el
detector perdía el R de los últimos milisegundos o ponía uno fantasma sobre
el QRS cortado, la referencia de prematuridad de los últimos latidos repetía
el último R-R, y la última ventana se juzgaba con el filtro sin asentar. Lo
que el bloque decide ahí es final —el siguiente lo tiene en su contexto y no
lo vuelve a contar—, así que cada borde sumaba o perdía un latido, metía un
ectópico en el RMSSD o daba vuelta una ventana. Con el contexto derecho
(`settings.ml_analysis_lookahead_seconds`) esa franja es interior en el bloque
que la cuenta.

Lo que el contexto no vuelve idéntico es el detector: NeuroKit descarta las
regiones de QRS más cortas que el 40 % de la media **del bloque**, así que en
ECG real ruidoso algún R se corre o aparece según qué más haya en el bloque,
lejos de cualquier borde (MIT-BIH 203: 2 de ~2.300). No es un error de
atribución: cada R que un bloque detecta se cuenta una sola vez.

Las claves son estables porque se guardan en `study.ml_state` y se suman contra
las de bloques que corrieron con otra versión del código. Todo se calcula en
float64: Σx² de un millón de intervalos de 800 ms son 6·10¹¹ ms², y en float32
la resta de la varianza se quedaría sin dígitos.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np

from app.db.models.signal_quality import SignalQualityLevel
from app.ml.contracts import Mask, QualityWindow
from app.ml.hrv import RRSeries, counted_intervals, nn_milliseconds, successive_pairs

#: Prefijo de los conteos de ventanas por motivo (`reason.ok`, `reason.psqi`…).
REASON_PREFIX = "reason."
#: Prefijo de las proporciones por motivo en el resumen.
REASON_RATIO_PREFIX = "reasonRatio."

#: Las dos únicas claves que no se suman: son extremos.
HR_MIN_KEY = "hrMinBpm"
HR_MAX_KEY = "hrMaxBpm"

#: Intervalos contados que tiene que tener una ventana buena para entrar en la
#: frecuencia mínima y máxima del estudio. Con menos, la "frecuencia de la
#: ventana" es la de dos o tres latidos, y un solo intervalo mal medido la
#: vuelve el extremo de quince días de registro.
HR_WINDOW_MIN_INTERVALS = 5

#: Diferencia sucesiva, en ms, por encima de la cual cuenta para el pNN50.
NN50_MS = 50.0

_LEVEL_KEYS = {
    SignalQualityLevel.GOOD: "windowsGood",
    SignalQualityLevel.MARGINAL: "windowsMarginal",
    SignalQualityLevel.BAD: "windowsBad",
}


def block_totals(
    rr: RRSeries,
    windows: Iterable[QualityWindow],
    analyzable: Mask,
    *,
    context_samples: int,
    n_samples: int,
    sample_rate: int,
    lookahead_samples: int = 0,
) -> dict[str, float]:
    """Las sumas de la parte nueva de un bloque. Coordenadas **relativas al bloque**.

    La parte nueva es `[context_samples, n_samples - lookahead_samples)`: lo de
    los dos lados es contexto. `windows` son solo las ventanas que el bloque
    informa (las de la parte nueva) y `analyzable` la máscara final, con los
    empalmes ya excluidos: un R cuenta como latido si cae en la parte nueva y
    en señal analizable.
    """
    end = max(n_samples - max(lookahead_samples, 0), context_samples)
    new_samples = max(end - context_samples, 0)
    counted = counted_intervals(rr, context_samples, end)
    nn_ms = nn_milliseconds(rr)
    counted_nn = nn_ms[counted]
    pairs = successive_pairs(rr, counted)
    diffs = np.diff(nn_ms)[pairs[1:]]

    new_peaks = rr.rpeaks[(rr.rpeaks >= context_samples) & (rr.rpeaks < end)]
    if analyzable.size == n_samples:
        new_peaks = new_peaks[analyzable[new_peaks]]

    totals: dict[str, float] = {
        "analyzedSamples": float(new_samples),
        "analyzedSeconds": new_samples / float(sample_rate),
        "beats": float(new_peaks.size),
        "nnCount": float(counted_nn.size),
        "nnSumMs": float(np.sum(counted_nn)),
        "nnSumSqMs2": float(np.sum(counted_nn**2)),
        "diffCount": float(diffs.size),
        "diffSumSqMs2": float(np.sum(diffs**2)),
        "nn50Count": float(np.count_nonzero(np.abs(diffs) > NN50_MS)),
        "windows": 0.0,
        "windowsGood": 0.0,
        "windowsMarginal": 0.0,
        "windowsBad": 0.0,
    }

    # El intervalo se ubica en la ventana de su latido final, la misma regla que
    # decide en qué bloque se cuenta.
    end_peaks = rr.rpeaks[1:]
    rates: list[float] = []
    for window in windows:
        totals["windows"] += 1.0
        level_key = _LEVEL_KEYS.get(window.level)
        if level_key is not None:
            totals[level_key] += 1.0
        reason_key = f"{REASON_PREFIX}{window.reason}"
        totals[reason_key] = totals.get(reason_key, 0.0) + 1.0
        if window.level is not SignalQualityLevel.GOOD or end_peaks.size == 0:
            continue
        low = int(np.searchsorted(end_peaks, window.start_sample, side="left"))
        high = int(
            np.searchsorted(end_peaks, window.start_sample + window.length_samples, side="left")
        )
        in_window = nn_ms[low:high][counted[low:high]]
        if in_window.size >= HR_WINDOW_MIN_INTERVALS:
            mean_ms = float(np.mean(in_window))
            if mean_ms > 0:
                rates.append(60_000.0 / mean_ms)
    if rates:
        totals[HR_MIN_KEY] = min(rates)
        totals[HR_MAX_KEY] = max(rates)
    return totals


def combine_totals(a: Mapping[str, float], b: Mapping[str, float]) -> dict[str, float]:
    """Suma dos juegos de totales. Asociativa y conmutativa.

    Todo se suma salvo la frecuencia mínima y la máxima, que toman el extremo.
    Una clave que falta en uno de los dos vale lo que trae el otro: un estado
    guardado antes de que existiera la clave, o un bloque sin ventanas buenas,
    no tienen que inventar un cero —un cero en `hrMinBpm` sería la frecuencia
    mínima del estudio—.
    """
    combined = {key: float(value) for key, value in a.items()}
    for key, raw in b.items():
        value = float(raw)
        if key not in combined:
            combined[key] = value
        elif key == HR_MIN_KEY:
            combined[key] = min(combined[key], value)
        elif key == HR_MAX_KEY:
            combined[key] = max(combined[key], value)
        else:
            combined[key] += value
    return combined


def summary_from_totals(totals: Mapping[str, float]) -> dict[str, float]:
    """El resumen legible: las mismas claves que `hrv_summary` y las de calidad.

    Las métricas que no se pueden calcular quedan **afuera** en vez de en cero:
    el SDNN necesita dos intervalos, el RMSSD una diferencia, y "0 ms" se lee
    como un corazón sin variabilidad, que es un hallazgo y no una ausencia de
    datos. Las proporciones de calidad sí van siempre, en cero sin ventanas,
    como antes en `ml_state.metrics`.
    """

    def get(key: str) -> float:
        return float(totals.get(key, 0.0))

    windows = get("windows")
    summary: dict[str, float] = {
        "beats": get("beats"),
        "windows": windows,
        "goodRatio": _ratio(get("windowsGood"), windows),
        "marginalRatio": _ratio(get("windowsMarginal"), windows),
        "badRatio": _ratio(get("windowsBad"), windows),
        "analyzedHours": round(get("analyzedSeconds") / 3600.0, 6),
    }
    for key in sorted(totals):
        if key.startswith(REASON_PREFIX):
            reason = key[len(REASON_PREFIX) :]
            summary[f"{REASON_RATIO_PREFIX}{reason}"] = _ratio(get(key), windows)

    count = get("nnCount")
    if count >= 1:
        mean_ms = get("nnSumMs") / count
        summary["meanNNms"] = round(mean_ms, 3)
        if mean_ms > 0:
            summary["meanBpm"] = round(60_000.0 / mean_ms, 3)
    if count >= 2:
        # Desvío muestral desde los momentos. La resta puede dar un negativo
        # ínfimo por redondeo cuando todos los intervalos son iguales: es cero.
        variance = (get("nnSumSqMs2") - get("nnSumMs") ** 2 / count) / (count - 1)
        summary["sdnnMs"] = round(float(np.sqrt(max(variance, 0.0))), 3)
    diff_count = get("diffCount")
    if diff_count >= 1:
        summary["rmssdMs"] = round(float(np.sqrt(max(get("diffSumSqMs2"), 0.0) / diff_count)), 3)
        summary["pnn50"] = round(get("nn50Count") / diff_count, 6)
    if HR_MIN_KEY in totals:
        summary["minBpm"] = round(get(HR_MIN_KEY), 3)
    if HR_MAX_KEY in totals:
        summary["maxBpm"] = round(get(HR_MAX_KEY), 3)
    return summary


def _ratio(part: float, total: float) -> float:
    return round(part / total, 6) if total > 0 else 0.0
