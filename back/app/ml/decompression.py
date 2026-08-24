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
import zlib
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------- #
# Constantes del formato (include/config.h)
# --------------------------------------------------------------------------- #
FRAME_BYTES = 256
HEADER_BYTES = 24
CRC_OFFSET = 20
RUN_BYTES = 3  # uint8 flags + uint16 largo
MAX_FLAG_RUNS = 24
FRAME_MAGIC = 0xEC61
FRAME_VERSION = 1

PREDICTOR_ORDER = 2
RICE_ESCAPE_Q = 12
RICE_RAW_BITS = 23
RICE_ADAPT_SHIFT = 3
RICE_K_MAX = 24
RICE_SEED_MEAN = 8

SAMPLE_RATE_HZ = 500
STEP_MS = 1000 // SAMPLE_RATE_HZ  # división ENTERA, igual que el firmware

# Bits de hdrFlags (byte 3)
HDR_REASON_MASK = 0x03
HDR_DIAGNOSTIC = 0x04
HDR_SIMULATED = 0x08
HDR_BOOTID_MASK = 0xF0
HDR_BOOTID_SHIFT = 4

# Motivo de cierre de la trama
CLOSE_FULL, CLOSE_GAP, CLOSE_FLUSH, CLOSE_RUNS = 0, 1, 2, 3

# Bits de flags por muestra
FLAG_LEAD_OFF = 0x01
FLAG_R_PEAK = 0x02
FLAG_EVENT_MARKER = 0x04
FLAG_RLD_OFF = 0x08
FLAG_ADC_SATURATED = 0x10
FLAG_LEAD_OFF_CH2 = 0x20
FLAG_SQI_MASK = 0xC0
FLAG_SQI_SHIFT = 6

SQ_UNKNOWN, SQ_BAD, SQ_MARGINAL, SQ_GOOD = 0, 1, 2, 3


class FrameError(ValueError):
    """La trama no es utilizable: magic, versión, CRC o consistencia interna.

    Una trama que levanta esto **no se guarda y no se confirma por ACK**: el
    equipo la va a retransmitir. Nunca se recupera parcialmente — serían datos
    inventados presentados como señal del paciente.
    """


def frame_crc(frame: bytes) -> int:
    """CRC-32 (IEEE 802.3) de la trama entera salteando los 4 bytes del CRC.

    Es el mismo `zlib.crc32` que nombra `INTEGRACION.md` §4.2. Cubre también el
    relleno en cero, así que detecta basura en la zona no usada de la trama.
    """
    return zlib.crc32(frame[CRC_OFFSET + 4 :], zlib.crc32(frame[:CRC_OFFSET]))


@dataclass(frozen=True)
class FrameInfo:
    seq: int
    t0_ms: int
    n_samples: int
    duration_ms: int
    streams: int
    n_channels: int
    includes_diagnostic: bool
    close_reason: int
    simulated: bool
    boot_id: int

    @property
    def expected_duration_ms(self) -> int:
        """Duración que tendría la trama si no faltara ninguna muestra."""
        return (self.n_samples - 1) * STEP_MS if self.n_samples > 1 else 0

    @property
    def internal_gap_ms(self) -> int:
        """Milisegundos de señal que FALTAN dentro de esta trama (0 = ninguno).

        Es exacto al milisegundo. Lo único que no se puede saber es en qué punto
        de la trama estaba el hueco. Un hueco no es una línea isoeléctrica: es
        información clínica y hay que guardarla.
        """
        return max(0, self.duration_ms - self.expected_duration_ms)


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


def read_header(frame: bytes) -> FrameInfo:
    """Valida magic / versión / CRC-32 / consistencia y devuelve la cabecera.

    Es el ÚNICO criterio de "esta trama sirve": si pasa, se puede decodificar y
    archivar; si no pasa, se descarta entera y no se confirma. El orden de los
    chequeos es el de `INTEGRACION.md` §4.2 y no es casual: no se toca el
    contenido de una trama cuya versión no se entiende.
    """
    if len(frame) != FRAME_BYTES:
        raise FrameError(f"la trama debe tener {FRAME_BYTES} bytes, llegaron {len(frame)}")

    magic = struct.unpack_from("<H", frame, 0)[0]
    if magic != FRAME_MAGIC:
        raise FrameError(f"magic inválido: 0x{magic:04X} (esperado 0x{FRAME_MAGIC:04X})")
    if frame[2] != FRAME_VERSION:
        raise FrameError(
            f"versión de trama {frame[2]} desconocida (este lector entiende la "
            f"{FRAME_VERSION}); NO interpretar el contenido"
        )

    crc_declarado = struct.unpack_from("<I", frame, CRC_OFFSET)[0]
    crc_real = frame_crc(frame)
    if crc_declarado != crc_real:
        raise FrameError(
            f"CRC-32 no coincide (declarado 0x{crc_declarado:08X}, calculado 0x{crc_real:08X})"
        )

    hdr = frame[3]
    includes_diagnostic = bool(hdr & HDR_DIAGNOSTIC)
    streams = frame[19]
    if streams == 0:
        raise FrameError("streams = 0")
    if includes_diagnostic and streams % 2 != 0:
        raise FrameError("streams impar con diagnostic incluido")
    n_channels = streams // 2 if includes_diagnostic else streams
    if n_channels == 0:
        raise FrameError("n_channels = 0")

    bit_bytes = struct.unpack_from("<H", frame, 16)[0]
    run_count = frame[18]
    if run_count == 0:
        raise FrameError("run_count = 0 (toda trama con muestras tiene al menos una corrida)")
    if run_count > MAX_FLAG_RUNS:
        raise FrameError(f"run_count {run_count} > {MAX_FLAG_RUNS}")
    if HEADER_BYTES + bit_bytes + run_count * RUN_BYTES > FRAME_BYTES:
        raise FrameError("el bitstream y las corridas se superponen")

    n_samples = struct.unpack_from("<H", frame, 12)[0]
    if n_samples == 0:
        raise FrameError("n_samples = 0")

    return FrameInfo(
        seq=struct.unpack_from("<I", frame, 4)[0],
        t0_ms=struct.unpack_from("<I", frame, 8)[0],
        n_samples=n_samples,
        duration_ms=struct.unpack_from("<H", frame, 14)[0],
        streams=streams,
        n_channels=n_channels,
        includes_diagnostic=includes_diagnostic,
        close_reason=hdr & HDR_REASON_MASK,
        simulated=bool(hdr & HDR_SIMULATED),
        boot_id=(hdr & HDR_BOOTID_MASK) >> HDR_BOOTID_SHIFT,
    )


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


def iter_frames(payload: bytes) -> list[bytes]:
    """Parte un cuerpo de ingesta en tramas de 256 B.

    No valida el contenido: eso es trabajo de `read_header`. Solo exige que el
    largo sea múltiplo del tamaño de trama.
    """
    if not payload or len(payload) % FRAME_BYTES != 0:
        raise FrameError(f"el cuerpo debe ser un múltiplo de {FRAME_BYTES} bytes")
    return [payload[i : i + FRAME_BYTES] for i in range(0, len(payload), FRAME_BYTES)]
