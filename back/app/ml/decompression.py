"""Decodificador de las tramas comprimidas del Holter.

Port del decodificador de **referencia** del firmware
(`../Holter-ECG-System/tools/holter_frame_decoder.py`, a su vez traducción
literal del decodificador normativo de `include/EcgFrameCodec.h`). El original
está verificado muestra a muestra contra el codificador del firmware sobre los
registros de MIT-BIH y NSTDB, así que este archivo se porta **verbatim en su
lógica**: si algo acá parece raro, casi seguro es intencional y está explicado.

Formato (256 B, little-endian, `INTEGRACION.md` §3-4):

```
[0..23]                     cabecera
[24 .. 24+bitBytes-1]       bitstream Rice (MSB primero)
[...ceros...]               relleno (entra en el CRC)
[256-runCount*3 .. 255]     corridas RLE de flags: uint8 flags + uint16 LE largo
```

Las constantes tienen que coincidir con `include/config.h` del firmware que
grabó el estudio. No se inventan: si cambian, sube `FRAME_VERSION` y este
decodificador **rechaza** lo que no entiende en vez de devolver señal
incorrecta.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

# Re-exportados para no romper a los importadores existentes: el parser de
# cabecera vive ahora en un módulo sin numpy (ver su docstring), pero
# `from app.ml.decompression import read_header, FrameError, ...` sigue andando.
from app.ml.frame_header import (
    CLOSE_FLUSH,
    CLOSE_FULL,
    CLOSE_GAP,
    CLOSE_RUNS,
    CRC_OFFSET,
    FLAG_ADC_SATURATED,
    FLAG_EVENT_MARKER,
    FLAG_LEAD_OFF,
    FLAG_LEAD_OFF_CH2,
    FLAG_R_PEAK,
    FLAG_RLD_OFF,
    FLAG_SQI_MASK,
    FLAG_SQI_SHIFT,
    FRAME_BYTES,
    FRAME_MAGIC,
    FRAME_VERSION,
    HDR_BOOTID_MASK,
    HDR_BOOTID_SHIFT,
    HDR_DIAGNOSTIC,
    HDR_REASON_MASK,
    HDR_SIMULATED,
    HEADER_BYTES,
    MAX_FLAG_RUNS,
    PREDICTOR_ORDER,
    RICE_ADAPT_SHIFT,
    RICE_ESCAPE_Q,
    RICE_K_MAX,
    RICE_RAW_BITS,
    RICE_SEED_MEAN,
    RUN_BYTES,
    SAMPLE_RATE_HZ,
    SQ_BAD,
    SQ_GOOD,
    SQ_MARGINAL,
    SQ_UNKNOWN,
    STEP_MS,
    FrameError,
    FrameInfo,
    frame_crc,
    iter_frames,
    read_header,
)

__all__ = [
    "CLOSE_FLUSH",
    "CLOSE_FULL",
    "CLOSE_GAP",
    "CLOSE_RUNS",
    "CRC_OFFSET",
    "FLAG_ADC_SATURATED",
    "FLAG_EVENT_MARKER",
    "FLAG_LEAD_OFF",
    "FLAG_LEAD_OFF_CH2",
    "FLAG_R_PEAK",
    "FLAG_RLD_OFF",
    "FLAG_SQI_MASK",
    "FLAG_SQI_SHIFT",
    "FRAME_BYTES",
    "FRAME_MAGIC",
    "FRAME_VERSION",
    "HDR_BOOTID_MASK",
    "HDR_BOOTID_SHIFT",
    "HDR_DIAGNOSTIC",
    "HDR_REASON_MASK",
    "HDR_SIMULATED",
    "HEADER_BYTES",
    "MAX_FLAG_RUNS",
    "PREDICTOR_ORDER",
    "RICE_ADAPT_SHIFT",
    "RICE_ESCAPE_Q",
    "RICE_K_MAX",
    "RICE_RAW_BITS",
    "RICE_SEED_MEAN",
    "RUN_BYTES",
    "SAMPLE_RATE_HZ",
    "SQ_BAD",
    "SQ_GOOD",
    "SQ_MARGINAL",
    "SQ_UNKNOWN",
    "STEP_MS",
    "DecodedFrame",
    "FrameError",
    "FrameInfo",
    "decode_frame",
    "frame_crc",
    "iter_frames",
    "read_header",
]


@dataclass(frozen=True)
class DecodedFrame:
    info: FrameInfo
    #: `(n_channels, n_samples)` en µV. int32 y no int16 **a propósito**: el
    #: front-end es DC-acoplado y el potencial de media celda de los electrodos
    #: (±300 mV según las normas de ECG) entra entero al ADC. Truncar a 16 bits
    #: destruye el registro de cualquier paciente con más de 33 mV de offset.
    raw_uV: np.ndarray
    #: `(n_channels, n_samples)` o `(0, n_samples)` si la trama no lo trae.
    diagnostic_uV: np.ndarray
    #: `(n_samples,)` uint8 con los bits de `FLAG_*`.
    flags: np.ndarray

    @property
    def timestamps_ms(self) -> np.ndarray:
        """Grilla **nominal** `t0_ms + i*STEP_MS`, con el wraparound de uint32.

        No se guarda el `millis()` individual de cada muestra: el ADS1292R
        convierte a intervalo fijo con su propio oscilador, así que ese jitter
        de ±1 ms es ruido del reloj del micro, no una propiedad de la señal.
        """
        idx = np.arange(self.info.n_samples, dtype=np.uint64)
        return ((np.uint64(self.info.t0_ms) + idx * np.uint64(STEP_MS)) & 0xFFFFFFFF).astype(
            np.uint32
        )


class _BitReader:
    """Lector de bits MSB primero, igual que el `BitWriter` del firmware."""

    __slots__ = ("_buf", "_pos", "_limit")

    def __init__(self, buf: bytes, bit_limit: int) -> None:
        self._buf = buf
        self._pos = 0
        self._limit = bit_limit

    def read_bit(self) -> int:
        if self._pos >= self._limit:
            raise FrameError("bitstream truncado")
        bit = (self._buf[self._pos >> 3] >> (7 - (self._pos & 7))) & 1
        self._pos += 1
        return bit

    def read_bits(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | self.read_bit()
        return value


class _RiceStream:
    """Estado adaptativo de UN flujo de residuos.

    Codificador y decodificador lo actualizan con las MISMAS operaciones
    enteras, por eso el parámetro `k` nunca hace falta transmitirlo y las dos
    puntas no pueden divergir (no interviene ni un float).
    """

    __slots__ = ("prev", "prev2", "sum", "primed", "primed2")

    def __init__(self) -> None:
        self.prev = 0
        self.prev2 = 0
        self.sum = RICE_SEED_MEAN << RICE_ADAPT_SHIFT
        self.primed = False
        self.primed2 = False

    def k(self) -> int:
        mean = self.sum >> RICE_ADAPT_SHIFT
        k = 0
        while k < RICE_K_MAX and (1 << (k + 1)) <= mean:
            k += 1
        return k

    def predict(self) -> int:
        if PREDICTOR_ORDER >= 2 and self.primed2:
            return 2 * self.prev - self.prev2
        if self.primed:
            return self.prev
        return 0

    def push(self, value: int) -> None:
        if self.primed:
            self.prev2 = self.prev
            self.primed2 = True
        self.prev = value
        self.primed = True

    def adapt(self, u: int) -> None:
        self.sum = self.sum - (self.sum >> RICE_ADAPT_SHIFT) + u


def _unzigzag(u: int) -> int:
    return (u >> 1) ^ -(u & 1)


def decode_frame(frame: bytes) -> DecodedFrame:
    """Decodifica una trama de 256 bytes.

    La reconstrucción de `raw_uV` y de `flags` es EXACTA (sin pérdida): es la
    misma secuencia de enteros que tenía el equipo en RAM.
    """
    info = read_header(frame)

    bit_bytes = struct.unpack_from("<H", frame, 16)[0]
    run_count = frame[18]
    runs_off = FRAME_BYTES - run_count * RUN_BYTES

    reader = _BitReader(frame[HEADER_BYTES:], bit_bytes * 8)
    streams = [_RiceStream() for _ in range(info.streams)]

    n_diag = info.n_channels if info.includes_diagnostic else 0
    raw = np.zeros((info.n_channels, info.n_samples), dtype=np.int32)
    diagnostic = np.zeros((n_diag, info.n_samples), dtype=np.int32)
    flags = np.zeros(info.n_samples, dtype=np.uint8)

    run_idx = 0
    run_remaining = struct.unpack_from("<H", frame, runs_off + 1)[0]

    for i in range(info.n_samples):
        for j, stream in enumerate(streams):
            # Prefijo unario: ceros hasta el 1, o hasta la marca de escape.
            q = 0
            while q < RICE_ESCAPE_Q:
                if reader.read_bit():
                    break
                q += 1

            k = stream.k()
            if q >= RICE_ESCAPE_Q:
                u = reader.read_bits(RICE_RAW_BITS)
            else:
                rem = reader.read_bits(k) if k > 0 else 0
                u = (q << k) | rem

            value = stream.predict() + _unzigzag(u)
            # Espejo EXACTO del codificador: la primera muestra de la trama NO
            # alimenta la media adaptativa — su "residuo" es en realidad el valor
            # absoluto de la señal y no dice nada sobre la actividad del tramo.
            # Si se incluye, `k` se dispara y el resto de la trama se decodifica
            # mal. Es el error de portabilidad más fácil de cometer.
            era_primed = stream.primed
            stream.push(value)
            if era_primed:
                stream.adapt(u)

            if j < info.n_channels:
                raw[j, i] = value
            else:
                diagnostic[j - info.n_channels, i] = value

        # Flags desde las corridas RLE.
        while run_remaining == 0 and run_idx + 1 < run_count:
            run_idx += 1
            run_remaining = struct.unpack_from("<H", frame, runs_off + run_idx * RUN_BYTES + 1)[0]
        if run_remaining == 0:
            raise FrameError("corridas de flags inconsistentes con n_samples")
        flags[i] = frame[runs_off + run_idx * RUN_BYTES]
        run_remaining -= 1

    return DecodedFrame(info=info, raw_uV=raw, diagnostic_uV=diagnostic, flags=flags)
