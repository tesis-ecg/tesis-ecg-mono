"""Detección de complejos QRS y acuerdo entre detectores.

**El hallazgo que sostiene el gate de calidad.** El firmware ya corre su propio
detector de R y marca `FLAG_R_PEAK` (bit 1) muestra por muestra dentro de cada
trama. Ese bit viajaba desde siempre en el payload y **no tenía ningún
consumidor**: se decodificaba y se tiraba.

Sirve para lo único que no se puede comprar de una biblioteca: un **segundo
detector independiente**. El del equipo es un FIR de banda con umbral adaptativo
sobre el MCU; el de la nube es el de NeuroKit, gradiente y umbral móvil sobre la
señal filtrada. Son familias distintas, y su grado de acuerdo —el bSQI— es la
única medida que nota que un tramo *no tiene latidos*.

Hace falta porque `nk.ecg_quality(method="zhao2018")` no lo nota: medido, le da
`Excellent` a ruido gaussiano puro sin un solo QRS. No es un bug nuestro — la
implementación de NeuroKit descartó el índice qSQI del paper original, que era
justo el que medía coincidencia entre detectores. El bSQI lo recupera gratis.
"""

from __future__ import annotations

import numpy as np

from app.ml.contracts import Flags, Indices, Signal
from app.ml.decompression import FLAG_R_PEAK


def firmware_rpeaks(flags: Flags) -> Indices:
    """Índices donde el firmware marcó un R, uno por complejo.

    El flag viaja comprimido en corridas RLE, así que un mismo latido puede
    llegar marcado en varias muestras seguidas. Se toma el **inicio** de cada
    corrida: contar cada muestra sería contar el mismo latido varias veces y
    hundiría el bSQI sin que haya ningún desacuerdo real.
    """
    marked = (flags & FLAG_R_PEAK) != 0
    if not marked.any():
        return np.empty(0, dtype=np.int64)
    starts = np.flatnonzero(marked & ~np.concatenate(([False], marked[:-1])))
    return starts.astype(np.int64)


def compensate_firmware_peaks(
    peaks: Indices, *, lag_samples: int, refractory_samples: int
) -> Indices:
    """Lleva los picos del firmware al pico real y descarta sus dobletes.

    Dos correcciones medidas sobre el chaleco por el equipo de firmware
    (2026-09-03), en este orden:

    1. **Dobletes.** El detector del MCU tiene 200 ms de refractario absoluto:
       confirma, queda ciego, y en cuanto se destraba vuelve a confirmar sobre la
       cola del mismo complejo — 31 de 110 intervalos por debajo de 300 ms en la
       captura con gel. Se queda el **primero** de cada grupo, que es el que
       corresponde al latido; el segundo es timbrado del filtro.
    2. **Retardo.** El bit no marca el pico sino la muestra donde el detector lo
       confirma, 200-300 ms más tarde. Se resta el retardo nominal para que los
       dos trenes hablen del mismo instante.

    Ambas tocan **solo** el tren que se compara contra NeuroKit para el bSQI. Los
    hallazgos de ritmo salen del otro detector, así que ni el refractario ni el
    corrimiento pueden inventar ni esconder un latido en el informe del médico.
    """
    if peaks.size == 0:
        return peaks
    if refractory_samples > 0:
        kept = [int(peaks[0])]
        for peak in peaks[1:]:
            if int(peak) - kept[-1] >= refractory_samples:
                kept.append(int(peak))
        peaks = np.array(kept, dtype=np.int64)
    shifted = peaks - lag_samples
    # Los latidos de los primeros `lag_samples` del lote se confirmaron en el
    # lote anterior: acá no tienen contraparte y se descartan.
    return shifted[shifted >= 0].astype(np.int64)


def detect_rpeaks(cleaned: Signal, sample_rate: int) -> Indices:
    """R-peaks de NeuroKit sobre señal ya filtrada.

    Devuelve vacío en vez de propagar el error cuando la señal es demasiado
    corta o degenerada: una ventana sin latidos es un resultado válido del gate
    de calidad, no una falla del lote.

    **Sin la corrección de artefactos de NeuroKit** (`correct_artifacts=False`).
    La corrección de Kubios no mira la señal: mira la serie R-R y, donde un
    intervalo se aparta del ritmo, **mueve o inserta** el R en la posición que
    interpola. Eso es justo lo que este motor existe para informar:

    - Una pausa de 3 s se lee como latidos perdidos y se rellena con R en el
      medio. Con variabilidad R-R real, o con un foco ectópico en el bloque, una
      pausa sinusal desaparecía entera sobre los bloques de 300 s
      (`test_ml_blocks.test_una_pausa_sobrevive_a_la_correccion_de_artefactos_en_un_bloque_largo`,
      `test_ml_block_invariance`).
    - Un ectópico prematuro con su pausa compensatoria es un par corto-largo: el
      R se corría al medio, el latido se recortaba fuera de su QRS y armaba
      plantillas fantasma. En MIT-BIH, sin la corrección, la precisión de las
      anomalías sube en los cuatro registros de carga alta (208 0,911 → 0,951;
      119 0,965 → 0,998; 233 0,952 → 0,999; 221 0,904 → 0,987) con el mismo
      recall, la pureza de clusters sube igual y los controles de carga baja
      bajan a 0 hallazgos/h (101 y 103 tenían 2 y 4). En las capturas del
      chaleco no cambia el nivel de ninguna ventana y las limpias quedan con
      menos plantillas (`gel_limpia` 3 → 1). El delineador de intervalos ya
      descartaba los R corridos (`intervals.IntervalThresholds.peak_tolerance_ms`).

    Lo que la corrección sí arreglaba —un latido que el detector no vio— queda
    acotado de otra forma: el R-R de una ventana mala ya es inválido (Etapa 1);
    taquicardia y bradicardia corren sobre la mediana móvil de 8 latidos, que
    absorbe un intervalo suelto; y por encima de 48 lpm un solo latido perdido
    no llega a los 2,5 s de `ml_pause_seconds`.
    """
    if cleaned.size < sample_rate:  # menos de un segundo: no hay nada que buscar
        return np.empty(0, dtype=np.int64)
    import neurokit2 as nk

    try:
        _, info = nk.ecg_peaks(cleaned, sampling_rate=sample_rate, correct_artifacts=False)
    except Exception:  # noqa: BLE001 — cualquier degeneración de la señal
        return np.empty(0, dtype=np.int64)
    peaks = info.get("ECG_R_Peaks") if isinstance(info, dict) else None
    if peaks is None:
        return np.empty(0, dtype=np.int64)
    # Coerción explícita: neurokit no trae stubs, así que todo lo suyo es `Any` y
    # `warn_return_any` de mypy strict rechaza devolverlo tal cual.
    array = np.asarray(peaks, dtype=np.int64).ravel()
    return array[(array >= 0) & (array < cleaned.size)]


def match_peaks(a: Indices, b: Indices, tolerance_samples: int) -> int:
    """Cuántos picos de `a` tienen pareja en `b` dentro de la tolerancia.

    Emparejamiento 1 a 1: cada pico de `b` se consume una sola vez. Sin eso, un
    detector que dispara tres veces sobre el mismo QRS sacaría un acuerdo
    perfecto contra un solo pico del otro.
    """
    if a.size == 0 or b.size == 0:
        return 0
    used = np.zeros(b.size, dtype=bool)
    matches = 0
    positions = np.searchsorted(b, a)
    for peak, position in zip(a, positions, strict=True):
        best_index = -1
        best_distance = tolerance_samples + 1
        for candidate in (position - 1, position, position + 1):
            if candidate < 0 or candidate >= b.size or used[candidate]:
                continue
            distance = abs(int(b[candidate]) - int(peak))
            if distance < best_distance:
                best_distance = distance
                best_index = candidate
        if best_index >= 0 and best_distance <= tolerance_samples:
            used[best_index] = True
            matches += 1
    return matches


def beat_sqi(a: Indices, b: Indices, tolerance_samples: int) -> float:
    """bSQI = 2·coincidencias / (|a| + |b|), en [0, 1].

    Vale 0 cuando alguno de los dos no encontró nada: un detector que no ve
    latidos donde el otro sí es exactamente el desacuerdo que interesa. Vale 0
    también con los dos vacíos, porque "ninguno vio nada" no es señal analizable.
    """
    total = a.size + b.size
    if total == 0:
        return 0.0
    return 2.0 * match_peaks(a, b, tolerance_samples) / total


def clean_signal(signal: Signal, sample_rate: int) -> Signal:
    """`nk.ecg_clean` con el tipo forzado a float32.

    NeuroKit devuelve float64; mantenerlo duplicaría la memoria de un lote de
    1,8 M de muestras sin ganar un solo bit de precisión útil sobre un ADC de 24
    bits.
    """
    import neurokit2 as nk

    if signal.size < sample_rate:
        return np.nan_to_num(signal, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    # Saneado ANTES de entrar a neurokit. Con NaN, `ecg_clean` intenta rellenar
    # por interpolación y revienta con `IndexError` cuando no queda ni un punto
    # válido del que interpolar — verificado. Un lote no puede fallar entero
    # porque diez segundos de señal sean basura: el gate de calidad ya sabe
    # llamar `bad` a una línea plana de ceros, y esa es la respuesta correcta.
    finite = np.nan_to_num(signal.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if not finite.any():
        return finite.astype(np.float32)
    cleaned = nk.ecg_clean(finite, sampling_rate=sample_rate)
    return np.asarray(cleaned, dtype=np.float32).ravel()
