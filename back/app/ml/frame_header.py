"""Cabecera y troceo de las tramas del Holter — **sin numpy**.

Separado de `decompression.py` a propósito. La ruta del ACK de
`POST /ingest/ecg-frames` solo necesita validar cabeceras y contar tramas, que
es `struct` + `zlib` de la biblioteca estándar; la señal se decodifica después,
en el procesamiento. Con todo junto, cada arranque en frío de la función
serverless importaba numpy para contestar un 202 que no lo usa (informe de
ingesta del 8/9/2026, hallazgo 2).

El contenido es el mismo de antes, movido verbatim: sigue siendo el port del
decodificador normativo del firmware (`INTEGRACION.md` §3-4). Las constantes
tienen que coincidir con `include/config.h` del firmware que grabó el estudio.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

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


def iter_frames(payload: bytes) -> list[bytes]:
    """Parte un cuerpo de ingesta en tramas de 256 B.

    No valida el contenido: eso es trabajo de `read_header`. Solo exige que el
    largo sea múltiplo del tamaño de trama.
    """
    if not payload or len(payload) % FRAME_BYTES != 0:
        raise FrameError(f"el cuerpo debe ser un múltiplo de {FRAME_BYTES} bytes")
    return [payload[i : i + FRAME_BYTES] for i in range(0, len(payload), FRAME_BYTES)]
