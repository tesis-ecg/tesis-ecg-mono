"""Codificador de tramas para los tests — port de `EcgFrameEncoder`.

Es la contraparte del decodificador de `app/ml/decompression.py` y una
traducción literal de `../Holter-ECG-System/include/EcgFrameCodec.h`
(`EcgFrameEncoder`, líneas 428-676).

**Para qué sirve y para qué NO.** Sirve para construir tramas válidas y tramas
deliberadamente rotas sin escribir bytes a mano en cada test. NO sirve como
prueba de que el formato está bien implementado: si este codificador y el
decodificador compartieran el mismo malentendido, el round-trip pasaría igual.
Esa verificación la da el **fixture dorado** (`tests/fixtures/frames_golden.bin`),
generado por el codificador de TypeScript del simulador, que es una tercera
implementación independiente.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from app.ml.decompression import (
    CLOSE_FULL,
    CLOSE_GAP,
    CLOSE_RUNS,
    CRC_OFFSET,
    FRAME_BYTES,
    FRAME_MAGIC,
    FRAME_VERSION,
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
    STEP_MS,
    frame_crc,
)

BOOTID_MODULO = 16
MAX_SAMPLE_GAP_MS = 6  # ECG_FRAME_MAX_SAMPLE_GAP_MS de config.h


def _zigzag(v: int) -> int:
    return ((v << 1) ^ (v >> 31)) & 0xFFFFFFFF


def _rice_k(mean: int) -> int:
    k = 0
    while k < RICE_K_MAX and (1 << (k + 1)) <= mean:
        k += 1
    return k


def _rice_bits(u: int, k: int) -> int:
    q = u >> k
    if q >= RICE_ESCAPE_Q:
        return RICE_ESCAPE_Q + RICE_RAW_BITS
    return q + 1 + k


class _BitWriter:
    """Escritor MSB primero. **Precondición: el buffer viene en cero.**

    Solo se hacen OR de los bits en 1 y la posición solo avanza, así que los
    ceros salen gratis — eso es lo que hace que el prefijo unario no cueste
    ninguna operación.
    """

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytearray, bit_pos: int) -> None:
        self.buf = buf
        self.pos = bit_pos

    def write_bits(self, value: int, nbits: int) -> None:
        for i in range(nbits - 1, -1, -1):
            if (value >> i) & 1:
                self.buf[self.pos >> 3] |= 0x80 >> (self.pos & 7)
            self.pos += 1

    def skip_zeros(self, n: int) -> None:
        self.pos += n


class _RiceStream:
    __slots__ = ("prev", "prev2", "sum", "primed", "primed2")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.prev = 0
        self.prev2 = 0
        self.sum = RICE_SEED_MEAN << RICE_ADAPT_SHIFT
        self.primed = False
        self.primed2 = False

    def k(self) -> int:
        return _rice_k(self.sum >> RICE_ADAPT_SHIFT)

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


@dataclass
class Sample:
    timestamp_ms: int
    raw_uV: list[int]
    flags: int = 0
    diagnostic_uV: list[int] = field(default_factory=list)


class FrameEncoder:
    """Port de `EcgFrameEncoder`. Una instancia = un flujo de tramas."""

    def __init__(
        self,
        *,
        n_channels: int = 1,
        store_diagnostic: bool = False,
        simulated: bool = True,
        first_seq: int = 0,
        boot_id: int = 0,
    ) -> None:
        self.n_channels = n_channels
        self.store_diagnostic = store_diagnostic
        self.simulated = simulated
        self.streams_count = n_channels * (2 if store_diagnostic else 1)
        self.max_samples = (FRAME_BYTES - HEADER_BYTES - RUN_BYTES) * 8 // self.streams_count
        self.seq = first_seq
        self.boot_id = boot_id % BOOTID_MODULO
        self._start_frame()

    # -- API ---------------------------------------------------------------- #

    @property
    def is_empty(self) -> bool:
        return self._n_samples == 0

    def set_seq(self, seq: int) -> None:
        self.seq = seq

    def set_boot_id(self, boot_id: int) -> None:
        self.boot_id = boot_id % BOOTID_MODULO

    def add_sample(self, sample: Sample) -> bool:
        """`False` (sin tocar nada) si la muestra no entra: cerrar y reintentar."""
        # 1. Continuidad temporal.
        if self._n_samples > 0:
            dt = (sample.timestamp_ms - self._t_last_ms) & 0xFFFFFFFF
            if dt > MAX_SAMPLE_GAP_MS:
                self._close_reason = CLOSE_GAP
                return False
            if ((sample.timestamp_ms - self._t0_ms) & 0xFFFFFFFF) > 0xFFFF:
                self._close_reason = CLOSE_GAP
                return False
        if self._n_samples >= self.max_samples:
            self._close_reason = CLOSE_FULL
            return False

        # 2. Corridas de flags (RLE).
        need_new_run = (
            self._run_count == 0
            or self._run_flags[self._run_count - 1] != sample.flags
            or self._run_len[self._run_count - 1] == 0xFFFF
        )
        new_run_count = self._run_count + (1 if need_new_run else 0)
        if new_run_count > MAX_FLAG_RUNS:
            self._close_reason = CLOSE_RUNS
            return False

        # 3. ¿Entra el costo en bits? Se calcula EXACTO antes de escribir un solo
        #    bit, por eso devolver False más arriba deja el estado intacto.
        values = self._gather(sample)
        us: list[int] = []
        ks: list[int] = []
        needed_bits = 0
        for j, stream in enumerate(self._streams):
            u = _zigzag(values[j] - stream.predict())
            k = stream.k()
            us.append(u)
            ks.append(k)
            needed_bits += _rice_bits(u, k)

        available_bits = (FRAME_BYTES - HEADER_BYTES - new_run_count * RUN_BYTES) * 8
        if self._bit_pos + needed_bits > available_bits:
            self._close_reason = CLOSE_FULL
            return False

        # 4. Commit.
        writer = _BitWriter(self._payload, self._bit_pos)
        for j, stream in enumerate(self._streams):
            u, k = us[j], ks[j]
            q = u >> k
            if q >= RICE_ESCAPE_Q:
                writer.skip_zeros(RICE_ESCAPE_Q)  # marca de escape
                writer.write_bits(u, RICE_RAW_BITS)
            else:
                writer.skip_zeros(q)  # prefijo unario
                writer.write_bits(1, 1)  # terminador
                if k > 0:
                    writer.write_bits(u & ((1 << k) - 1), k)
            # La PRIMERA muestra de la trama no alimenta la media adaptativa.
            was_primed = stream.primed
            stream.push(values[j])
            if was_primed:
                stream.adapt(u)
        self._bit_pos = writer.pos

        if need_new_run:
            self._run_flags.append(sample.flags)
            self._run_len.append(1)
            self._run_count = new_run_count
        else:
            self._run_len[self._run_count - 1] += 1

        if self._n_samples == 0:
            self._t0_ms = sample.timestamp_ms
        self._t_last_ms = sample.timestamp_ms
        self._n_samples += 1
        return True

    def close(self, reason: int | None = None) -> bytes | None:
        """Serializa la trama y arranca una nueva. `None` si no había muestras."""
        if self._n_samples == 0:
            return None

        close_reason = self._close_reason if reason is None else reason
        bit_bytes = (self._bit_pos + 7) // 8

        frame = bytearray(FRAME_BYTES)
        frame[HEADER_BYTES : HEADER_BYTES + len(self._payload)] = self._payload

        struct.pack_into("<H", frame, 0, FRAME_MAGIC)
        frame[2] = FRAME_VERSION
        frame[3] = (
            (close_reason & HDR_REASON_MASK)
            | (HDR_DIAGNOSTIC if self.store_diagnostic else 0)
            | (HDR_SIMULATED if self.simulated else 0)
            | ((self.boot_id << HDR_BOOTID_SHIFT) & 0xF0)
        )
        struct.pack_into("<I", frame, 4, self.seq & 0xFFFFFFFF)
        struct.pack_into("<I", frame, 8, self._t0_ms & 0xFFFFFFFF)
        struct.pack_into("<H", frame, 12, self._n_samples)
        struct.pack_into("<H", frame, 14, (self._t_last_ms - self._t0_ms) & 0xFFFF)
        struct.pack_into("<H", frame, 16, bit_bytes)
        frame[18] = self._run_count
        frame[19] = self.streams_count

        runs_off = FRAME_BYTES - self._run_count * RUN_BYTES
        for i in range(self._run_count):
            frame[runs_off + i * RUN_BYTES] = self._run_flags[i]
            struct.pack_into("<H", frame, runs_off + i * RUN_BYTES + 1, self._run_len[i])

        struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

        self.seq = (self.seq + 1) & 0xFFFFFFFF
        self._start_frame()
        return bytes(frame)

    # -- internos ----------------------------------------------------------- #

    def _start_frame(self) -> None:
        # El payload arranca en cero: el BitWriter cuenta con eso y el CRC cubre
        # la zona de relleno, así que tiene que ser determinística.
        self._payload = bytearray(FRAME_BYTES - HEADER_BYTES)
        self._bit_pos = 0
        self._n_samples = 0
        self._run_count = 0
        self._run_flags: list[int] = []
        self._run_len: list[int] = []
        self._t0_ms = 0
        self._t_last_ms = 0
        self._close_reason = CLOSE_FULL
        self._streams = [_RiceStream() for _ in range(self.streams_count)]

    def _gather(self, sample: Sample) -> list[int]:
        # Orden: primero los raw de cada canal, después los diagnostic.
        values = list(sample.raw_uV[: self.n_channels])
        if self.store_diagnostic:
            diagnostic = list(sample.diagnostic_uV[: self.n_channels])
            diagnostic += [0] * (self.n_channels - len(diagnostic))
            values += diagnostic
        return values


def encode_samples(
    samples: list[Sample],
    *,
    n_channels: int = 1,
    store_diagnostic: bool = False,
    simulated: bool = True,
    first_seq: int = 0,
    boot_id: int = 0,
) -> list[bytes]:
    """Codifica una lista de muestras en tantas tramas de 256 B como haga falta."""
    encoder = FrameEncoder(
        n_channels=n_channels,
        store_diagnostic=store_diagnostic,
        simulated=simulated,
        first_seq=first_seq,
        boot_id=boot_id,
    )
    frames: list[bytes] = []
    for sample in samples:
        if not encoder.add_sample(sample):
            frame = encoder.close()
            if frame is not None:
                frames.append(frame)
            if not encoder.add_sample(sample):
                raise RuntimeError("la muestra no entra ni en una trama vacía")
    last = encoder.close()
    if last is not None:
        frames.append(last)
    return frames


def synth_samples(
    count: int,
    *,
    t0_ms: int = 0,
    amplitude_uV: int = 900,
    period: int = 250,
    flags: int = 0,
    baseline_uV: int = 0,
    n_channels: int = 1,
) -> list[Sample]:
    """Muestras con forma de latido, deterministas y sin dependencias.

    No pretende ser un ECG realista — para eso está el generador del simulador.
    Acá lo único que importa es que la señal sea comprimible y reproducible.
    """
    samples: list[Sample] = []
    for i in range(count):
        phase = i % period
        if phase < 3:
            value = amplitude_uV
        elif phase < 6:
            value = -amplitude_uV // 3
        else:
            value = (phase % 11) - 5
        samples.append(
            Sample(
                timestamp_ms=t0_ms + i * STEP_MS,
                raw_uV=[baseline_uV + value] * n_channels,
                flags=flags,
            )
        )
    return samples


def corrupt_crc(frame: bytes) -> bytes:
    """Rompe el CRC declarado sin tocar el resto de la trama."""
    broken = bytearray(frame)
    declared = struct.unpack_from("<I", broken, CRC_OFFSET)[0]
    struct.pack_into("<I", broken, CRC_OFFSET, declared ^ 0xDEADBEEF)
    return bytes(broken)


def set_seq(frame: bytes, seq: int) -> bytes:
    """Reescribe `seq` recalculando el CRC (trama válida con otro número)."""
    patched = bytearray(frame)
    struct.pack_into("<I", patched, 4, seq & 0xFFFFFFFF)
    struct.pack_into("<I", patched, CRC_OFFSET, frame_crc(bytes(patched)))
    return bytes(patched)
