"""ECG sintético determinista para los tests del motor de detección.

`frame_builder.synth_samples` produce una onda comprimible y reproducible, que es
todo lo que necesitan los tests de codec e ingesta. El motor necesita otra cosa:
una señal con **morfología de latido real** —P, Q, R, S, T— porque lo que mide es
justamente si dos formas se parecen.

El latido es la misma suma de cinco gaussianas que usa el simulador de chaleco
del portal (`front/src/features/vest-simulator/codec/signal.ts`), para que lo que
los tests verifican sea lo mismo que se ve en el navegador.

Todo acá es determinista: mismo `seed`, misma señal, byte a byte.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.ml.decompression import FLAG_R_PEAK

SAMPLE_RATE = 500

#: Lo que tarda el detector del MCU en confirmar un latido después del pico:
#: 160 ms del FIR de 161 taps + 40 ms de la cascada + hasta 100 ms de ventana de
#: confirmación. Medido por el equipo de firmware sobre el chaleco el
#: 2026-09-03. El generador lo imita para que el `FLAG_R_PEAK` sintético caiga
#: donde cae el del equipo. Ponerlo en 0 da la señal idealizada de antes.
FIRMWARE_R_PEAK_LAG_MS = 250.0


@dataclass(frozen=True)
class SynthResult:
    signal_mv: np.ndarray
    flags: np.ndarray
    rpeaks: np.ndarray
    ectopic_peaks: np.ndarray


def _beat(
    t: np.ndarray, *, width: float = 1.0, invert_t: bool = False, amp: float = 1.0
) -> np.ndarray:
    def gauss(center: float, sigma: float, height: float) -> np.ndarray:
        return height * np.exp(-0.5 * ((t - center) / sigma) ** 2)

    return (
        gauss(-0.16, 0.025, 0.12)  # P
        + gauss(-0.02, 0.010 * width, -0.15 * amp)  # Q
        + gauss(0.0, 0.012 * width, 1.0 * amp)  # R
        + gauss(0.025, 0.012 * width, -0.25 * amp)  # S
        + gauss(0.20, 0.045, -0.25 if invert_t else 0.35)  # T
    )


def synth_ecg(
    duration_s: float = 60.0,
    *,
    bpm: float = 60.0,
    ectopic_every: int = 0,
    noise_uv: float = 8.0,
    seed: int = 7,
    sample_rate: int = SAMPLE_RATE,
    firmware_lag_ms: float = FIRMWARE_R_PEAK_LAG_MS,
) -> SynthResult:
    """ECG en mV con un foco ectópico opcional cada `ectopic_every` latidos.

    El ectópico son **tres cosas juntas**, no solo una forma distinta: llega
    prematuro (al 60 % del R-R), su QRS es tres veces más ancho y su onda T está
    invertida, y lo sigue una pausa compensatoria. Un ectópico que solo cambiara
    de forma sería indistinguible de un artefacto de movimiento, que es
    exactamente la confusión que el motor tiene que resolver.
    """
    rng = np.random.default_rng(seed)
    n = int(duration_s * sample_rate)
    signal = np.zeros(n, dtype=np.float64)
    flags = np.zeros(n, dtype=np.uint8)
    period = 60.0 / bpm
    half = sample_rate // 2
    lag = int(firmware_lag_ms * sample_rate / 1000.0)

    rpeaks: list[int] = []
    ectopics: list[int] = []
    time_s, index = 0.5, 0
    while time_s < duration_s - 1.0:
        is_ectopic = ectopic_every > 0 and index % ectopic_every == ectopic_every - 1
        center = int(time_s * sample_rate)
        low, high = max(center - half, 0), min(center + half, n)
        offsets = (np.arange(low, high) - center) / sample_rate
        signal[low:high] += _beat(
            offsets,
            width=3.0 if is_ectopic else 1.0,
            invert_t=is_ectopic,
            amp=1.3 if is_ectopic else 1.0,
        )
        # El flag NO va sobre el pico: va sobre la muestra donde el detector del
        # MCU confirma el latido. Marcarlo sobre el pico haría pasar el bSQI por
        # el motivo equivocado y taparía justo el defecto que hoy apaga el motor
        # sobre señal real. `rpeaks` sigue siendo la verdad de referencia.
        if center + lag < n:
            flags[center + lag] |= FLAG_R_PEAK
        rpeaks.append(center)
        if is_ectopic:
            ectopics.append(center)

        following_is_ectopic = (
            ectopic_every > 0 and (index + 1) % ectopic_every == ectopic_every - 1
        )
        if following_is_ectopic:
            time_s += period * 0.6  # el ectópico llega antes de tiempo
        elif is_ectopic:
            time_s += period * 1.4  # pausa compensatoria
        else:
            time_s += period
        index += 1

    signal += rng.normal(0.0, noise_uv / 1000.0, n)
    return SynthResult(
        signal_mv=signal.astype(np.float32),
        flags=flags,
        rpeaks=np.array(rpeaks, dtype=np.int64),
        ectopic_peaks=np.array(ectopics, dtype=np.int64),
    )


def to_microvolts(signal_mv: np.ndarray) -> list[int]:
    """A µV enteros, que es lo que entrega el AFE y espera el codificador."""
    return [int(round(value * 1000.0)) for value in signal_mv]
