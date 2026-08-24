"""Suite del decodificador de tramas.

Es la parte más crítica del pipeline: un bug acá no tira un 500, corrompe
silenciosamente señal de un paciente. Por eso los tests apuntan tanto a que lo
válido se reconstruya EXACTO como a que lo inválido se rechace entero, sin
recuperación parcial.
"""

import json
import pathlib
import struct
from typing import Any

import numpy as np
import pytest

from app.ml.decompression import (
    CLOSE_FLUSH,
    CLOSE_GAP,
    CRC_OFFSET,
    FLAG_ADC_SATURATED,
    FLAG_EVENT_MARKER,
    FLAG_LEAD_OFF,
    FLAG_SQI_SHIFT,
    FRAME_BYTES,
    HEADER_BYTES,
    MAX_FLAG_RUNS,
    RUN_BYTES,
    SQ_BAD,
    STEP_MS,
    FrameError,
    decode_frame,
    frame_crc,
    iter_frames,
    read_header,
)
from tests.frame_builder import (
    FrameEncoder,
    Sample,
    corrupt_crc,
    encode_samples,
    synth_samples,
)


def _one_frame(samples: list[Sample], **kwargs: object) -> bytes:
    frames = encode_samples(samples, **kwargs)  # type: ignore[arg-type]
    assert len(frames) == 1, f"se esperaba una sola trama, salieron {len(frames)}"
    return frames[0]


# --------------------------------------------------------------------------- #
# Round-trip exacto
# --------------------------------------------------------------------------- #


def test_round_trip_reproduces_every_sample_and_flag() -> None:
    samples = synth_samples(2000)
    for i in range(300, 400):
        samples[i].flags = FLAG_LEAD_OFF
    samples[900].flags = FLAG_EVENT_MARKER

    decoded_raw: list[int] = []
    decoded_flags: list[int] = []
    for frame in encode_samples(samples):
        decoded = decode_frame(frame)
        decoded_raw.extend(decoded.raw_uV[0].tolist())
        decoded_flags.extend(decoded.flags.tolist())

    assert decoded_raw == [s.raw_uV[0] for s in samples]
    assert decoded_flags == [s.flags for s in samples]


def test_round_trip_is_exact_over_random_signals() -> None:
    """Property-based ligero: ruido puro es el peor caso para el predictor."""
    rng = np.random.default_rng(20260821)
    for trial in range(12):
        values = rng.integers(-40_000, 40_000, size=600, dtype=np.int64).tolist()
        samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[int(v)]) for i, v in enumerate(values)]
        decoded: list[int] = []
        for frame in encode_samples(samples):
            decoded.extend(decode_frame(frame).raw_uV[0].tolist())
        assert decoded == values, f"trial {trial} no reprodujo la señal"


def test_first_sample_does_not_feed_the_adaptive_mean() -> None:
    """El error de portabilidad que `INTEGRACION.md` marca como el más fácil.

    Si la primera muestra alimentara la media adaptativa, `k` se dispararía y
    todo lo que sigue se decodificaría mal. Con un primer valor enorme y el
    resto casi plano, esa diferencia es imposible de no ver.
    """
    values = [1_500_000] + [10, 12, 11, 13, 10, 9, 11, 12] * 20
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(values)]
    frame = _one_frame(samples)

    assert decode_frame(frame).raw_uV[0].tolist() == values


def test_predictor_handles_zero_one_and_two_previous_samples() -> None:
    # Las tres primeras muestras ejercitan predict() sin previas, con una, y con
    # dos (que es donde entra 2*prev - prev2).
    values = [500, 1500, 2500, 3500, 4500]
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(values)]

    assert decode_frame(_one_frame(samples)).raw_uV[0].tolist() == values


def test_raw_values_beyond_int16_survive_intact() -> None:
    """±300 mV de offset de media celda es una condición normal, no una falla.

    Un int16 en µV solo llega a ±32,7 mV. Este test falla si alguien trunca.
    """
    values = [300_000, 299_500, -300_000, -299_000, 250_000, 0]
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(values)]
    decoded = decode_frame(_one_frame(samples))

    assert decoded.raw_uV.dtype == np.int32
    assert decoded.raw_uV[0].tolist() == values


def test_rice_escape_path_round_trips() -> None:
    """Un artefacto que lleva la señal al fondo de escala fuerza el escape.

    El escape son 12 ceros + 23 bits crudos, y es el camino que menos se
    ejercita en operación normal — justamente por eso hay que probarlo.
    """
    values = [0] * 10 + [1_500_000] + [0] * 6
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(values)]
    frame = _one_frame(samples)

    assert decode_frame(frame).raw_uV[0].tolist() == values
    # Sin escape, 17 muestras casi planas entrarían en pocos bytes. El salto
    # obliga a gastar 35 bits en tres muestras seguidas.
    assert struct.unpack_from("<H", frame, 16)[0] > 12


def test_residuals_are_bounded_by_the_23_bit_escape() -> None:
    """Límite real del formato, documentado como test para que no sorprenda.

    El escape transporta el residuo zigzagueado en `RICE_RAW_BITS` = 23 bits, o
    sea |residuo| < 2^22. El front-end nunca produce saltos así entre muestras
    consecutivas (el ADS1292R convierte a 500 SPS sobre una señal fisiológica),
    pero si alguien alimenta el codec con datos sintéticos fuera de rango, el
    valor se rompe en silencio. Este test fija dónde está el borde.
    """
    representable = [0, 0, 0, 2_000_000, 0, 0]
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(representable)]
    assert decode_frame(_one_frame(samples)).raw_uV[0].tolist() == representable

    # Un salto de ida y vuelta al fondo de escala sí excede el rango: el
    # predictor de orden 2 triplica el residuo y ya no entra en 23 bits.
    out_of_range = [0, 0, 0, 4_000_000, -4_000_000, 4_000_000]
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[v]) for i, v in enumerate(out_of_range)]
    assert decode_frame(_one_frame(samples)).raw_uV[0].tolist() != out_of_range


def test_two_channels_and_diagnostic_channel_round_trip() -> None:
    samples = [
        Sample(
            timestamp_ms=i * STEP_MS,
            raw_uV=[100 + i, -200 - i],
            diagnostic_uV=[10 + i, -20 - i],
        )
        for i in range(120)
    ]
    frame = _one_frame(samples, n_channels=2, store_diagnostic=True)
    decoded = decode_frame(frame)

    assert decoded.info.streams == 4
    assert decoded.info.n_channels == 2
    assert decoded.info.includes_diagnostic is True
    assert decoded.raw_uV[0].tolist() == [s.raw_uV[0] for s in samples]
    assert decoded.raw_uV[1].tolist() == [s.raw_uV[1] for s in samples]
    assert decoded.diagnostic_uV[0].tolist() == [s.diagnostic_uV[0] for s in samples]
    assert decoded.diagnostic_uV[1].tolist() == [s.diagnostic_uV[1] for s in samples]


# --------------------------------------------------------------------------- #
# Cabecera y metadatos
# --------------------------------------------------------------------------- #


def test_header_carries_seq_bootid_and_simulated_bit() -> None:
    frame = _one_frame(synth_samples(100), first_seq=41_235, boot_id=13, simulated=True)
    info = read_header(frame)

    assert info.seq == 41_235
    assert info.boot_id == 13
    assert info.simulated is True


def test_simulated_bit_can_be_turned_off() -> None:
    frame = _one_frame(synth_samples(50), simulated=False)

    assert read_header(frame).simulated is False


def test_boot_id_wraps_at_16() -> None:
    frame = _one_frame(synth_samples(50), boot_id=17)

    assert read_header(frame).boot_id == 1


def test_timestamps_follow_the_nominal_grid() -> None:
    frame = _one_frame(synth_samples(200, t0_ms=1_000_000))
    decoded = decode_frame(frame)

    expected = [1_000_000 + i * STEP_MS for i in range(decoded.info.n_samples)]
    assert decoded.timestamps_ms.tolist() == expected


def test_timestamps_wrap_like_uint32_millis() -> None:
    """A los 49,7 días `millis()` da la vuelta; el firmware calcula en uint32."""
    t0 = 0xFFFFFFFF - 10
    frame = _one_frame(synth_samples(20, t0_ms=t0))
    decoded = decode_frame(frame)

    assert decoded.timestamps_ms.max() <= 0xFFFFFFFF
    assert decoded.timestamps_ms.tolist()[:8] == [(t0 + i * STEP_MS) & 0xFFFFFFFF for i in range(8)]


def test_internal_gap_is_measured_to_the_millisecond() -> None:
    """Un salto chico no cierra la trama pero queda medido en durationMs."""
    samples = [Sample(timestamp_ms=i * STEP_MS, raw_uV=[i]) for i in range(50)]
    for sample in samples[25:]:
        sample.timestamp_ms += 4  # 4 ms de hueco, por debajo del umbral de cierre
    info = read_header(_one_frame(samples))

    assert info.n_samples == 50
    assert info.expected_duration_ms == 49 * STEP_MS
    assert info.internal_gap_ms == 4


def test_frame_without_gap_reports_zero() -> None:
    assert read_header(_one_frame(synth_samples(300))).internal_gap_ms == 0


def test_close_reason_gap_is_recorded() -> None:
    """Un salto grande cierra la trama y el motivo viaja en la cabecera."""
    encoder = FrameEncoder()
    for i in range(40):
        assert encoder.add_sample(Sample(timestamp_ms=i * STEP_MS, raw_uV=[i]))
    # 500 ms de salto: muy por encima de ECG_FRAME_MAX_SAMPLE_GAP_MS.
    assert encoder.add_sample(Sample(timestamp_ms=40 * STEP_MS + 500, raw_uV=[0])) is False
    frame = encoder.close()
    assert frame is not None

    assert read_header(frame).close_reason == CLOSE_GAP


def test_close_reason_flush_can_be_forced() -> None:
    encoder = FrameEncoder()
    for sample in synth_samples(30):
        assert encoder.add_sample(sample)
    frame = encoder.close(reason=CLOSE_FLUSH)
    assert frame is not None

    assert read_header(frame).close_reason == CLOSE_FLUSH


# --------------------------------------------------------------------------- #
# Corridas RLE de flags
# --------------------------------------------------------------------------- #


def test_single_flag_run() -> None:
    frame = _one_frame(synth_samples(200, flags=FLAG_LEAD_OFF))

    assert frame[18] == 1
    assert set(decode_frame(frame).flags.tolist()) == {FLAG_LEAD_OFF}


def test_max_flag_runs_closes_the_frame_with_reason_runs() -> None:
    """Señal tan inestable que se agotan las 24 corridas."""
    encoder = FrameEncoder()
    index = 0
    while encoder.add_sample(Sample(timestamp_ms=index * STEP_MS, raw_uV=[index], flags=index % 2)):
        index += 1
    frame = encoder.close()
    assert frame is not None

    assert frame[18] == MAX_FLAG_RUNS
    assert read_header(frame).close_reason == 3  # CLOSE_RUNS
    decoded = decode_frame(frame)
    assert decoded.flags.tolist() == [i % 2 for i in range(decoded.info.n_samples)]


def test_flag_runs_live_at_the_end_of_the_frame() -> None:
    samples = synth_samples(60)
    samples[30].flags = FLAG_ADC_SATURATED
    frame = _one_frame(samples)

    run_count = frame[18]
    runs_offset = FRAME_BYTES - run_count * RUN_BYTES
    total = sum(
        struct.unpack_from("<H", frame, runs_offset + i * RUN_BYTES + 1)[0]
        for i in range(run_count)
    )
    assert total == read_header(frame).n_samples


def test_sqi_bits_survive_the_round_trip() -> None:
    flags = (SQ_BAD << FLAG_SQI_SHIFT) | FLAG_LEAD_OFF
    frame = _one_frame(synth_samples(80, flags=flags))
    decoded = decode_frame(frame)

    assert int(decoded.flags[0]) == flags
    assert int(decoded.flags[0]) >> FLAG_SQI_SHIFT == SQ_BAD


# --------------------------------------------------------------------------- #
# Rechazos: una trama que no valida se descarta ENTERA
# --------------------------------------------------------------------------- #


def test_rejects_wrong_length() -> None:
    with pytest.raises(FrameError, match="256 bytes"):
        read_header(b"\x00" * 255)


def test_rejects_bad_magic() -> None:
    frame = bytearray(_one_frame(synth_samples(50)))
    struct.pack_into("<H", frame, 0, 0x1234)
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="magic"):
        read_header(bytes(frame))


def test_rejects_unknown_version_without_interpreting_content() -> None:
    """Versión desconocida = no se toca el contenido. No se 'intenta igual'."""
    frame = bytearray(_one_frame(synth_samples(50)))
    frame[2] = 2
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="versión de trama 2"):
        decode_frame(bytes(frame))


def test_rejects_bad_crc() -> None:
    with pytest.raises(FrameError, match="CRC-32"):
        read_header(corrupt_crc(_one_frame(synth_samples(50))))


def test_crc_covers_the_zero_padding() -> None:
    """El relleno entra en el CRC: basura en la zona no usada se detecta."""
    frame = bytearray(_one_frame(synth_samples(20)))
    bit_bytes = struct.unpack_from("<H", frame, 16)[0]
    padding_offset = HEADER_BYTES + bit_bytes + 4
    assert frame[padding_offset] == 0, "el test necesita apuntar a relleno en cero"
    frame[padding_offset] = 0xFF

    with pytest.raises(FrameError, match="CRC-32"):
        read_header(bytes(frame))


def test_rejects_zero_streams() -> None:
    frame = bytearray(_one_frame(synth_samples(50)))
    frame[19] = 0
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="streams = 0"):
        read_header(bytes(frame))


def test_rejects_odd_streams_with_diagnostic() -> None:
    frame = bytearray(_one_frame(synth_samples(50)))
    frame[3] |= 0x04  # HDR_DIAGNOSTIC
    frame[19] = 3
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="streams impar"):
        read_header(bytes(frame))


def test_rejects_zero_samples() -> None:
    frame = bytearray(_one_frame(synth_samples(50)))
    struct.pack_into("<H", frame, 12, 0)
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="n_samples = 0"):
        read_header(bytes(frame))


def test_rejects_zero_run_count() -> None:
    frame = bytearray(_one_frame(synth_samples(50)))
    frame[18] = 0
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="run_count = 0"):
        read_header(bytes(frame))


def test_rejects_run_count_over_the_maximum() -> None:
    frame = bytearray(_one_frame(synth_samples(50)))
    frame[18] = MAX_FLAG_RUNS + 1
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="run_count"):
        read_header(bytes(frame))


def test_rejects_bitstream_overlapping_the_flag_runs() -> None:
    """`bitBytes` que se pasa del espacio disponible: nunca leer fuera de rango."""
    frame = bytearray(_one_frame(synth_samples(50)))
    struct.pack_into("<H", frame, 16, FRAME_BYTES)
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="se superponen"):
        read_header(bytes(frame))


def test_rejects_truncated_bitstream() -> None:
    """`bitBytes` demasiado chico para las muestras que la cabecera declara."""
    frame = bytearray(_one_frame(synth_samples(300)))
    struct.pack_into("<H", frame, 16, 4)
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="truncado"):
        decode_frame(bytes(frame))


def test_rejects_flag_runs_inconsistent_with_n_samples() -> None:
    frame = bytearray(_one_frame(synth_samples(300)))
    runs_offset = FRAME_BYTES - frame[18] * RUN_BYTES
    struct.pack_into("<H", frame, runs_offset + 1, 2)  # dice 2 muestras, hay cientos
    struct.pack_into("<I", frame, CRC_OFFSET, frame_crc(bytes(frame)))

    with pytest.raises(FrameError, match="corridas de flags inconsistentes"):
        decode_frame(bytes(frame))


# --------------------------------------------------------------------------- #
# Troceo del cuerpo de ingesta
# --------------------------------------------------------------------------- #


def test_iter_frames_splits_on_256_byte_boundaries() -> None:
    frames = encode_samples(synth_samples(1500))
    assert len(iter_frames(b"".join(frames))) == len(frames)


def test_iter_frames_rejects_a_body_that_is_not_a_multiple() -> None:
    with pytest.raises(FrameError, match="múltiplo"):
        iter_frames(b"\x00" * 300)


def test_iter_frames_rejects_an_empty_body() -> None:
    with pytest.raises(FrameError, match="múltiplo"):
        iter_frames(b"")


# --------------------------------------------------------------------------- #
# Fixture dorado: verificación entre lenguajes
# --------------------------------------------------------------------------- #
# Todo lo de arriba usa `tests/frame_builder.py`, que es un port del CODIFICADOR
# del firmware hecho por la misma mano que este decodificador. Si los dos
# compartieran un malentendido, el round-trip pasaría igual.
#
# Estos tests cierran ese agujero: decodifican bytes producidos por una tercera
# implementación independiente — el codificador de TypeScript del simulador
# (`front/src/features/vest-simulator/codec/`) — y exigen reproducir sus
# muestras y sus flags sin una sola diferencia.
#
# Para regenerar el fixture después de un cambio deliberado del codec:
#   cd front && UPDATE_GOLDEN=1 npx vitest run \
#       src/features/vest-simulator/codec/goldenFrames.test.ts


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def golden() -> tuple[bytes, dict[str, Any]]:
    binary = FIXTURES / "frames_golden.bin"
    meta = FIXTURES / "frames_golden.json"
    if not binary.exists() or not meta.exists():
        pytest.fail(
            "Falta el fixture dorado. Generarlo con:\n"
            "  cd front && UPDATE_GOLDEN=1 npx vitest run "
            "src/features/vest-simulator/codec/goldenFrames.test.ts"
        )
    return binary.read_bytes(), json.loads(meta.read_text())


def test_golden_frames_decode_to_the_exact_expected_samples(
    golden: tuple[bytes, dict[str, Any]],
) -> None:
    """El test que verifica el port del codec a TypeScript.

    Si el codificador del simulador se desvía del formato del firmware — aunque
    sea en un bit del estado adaptativo — esto falla.
    """
    payload, meta = golden

    decoded_raw: list[int] = []
    decoded_flags: list[int] = []
    for frame in iter_frames(payload):
        decoded = decode_frame(frame)
        decoded_raw.extend(decoded.raw_uV[0].tolist())
        decoded_flags.extend(decoded.flags.tolist())

    assert decoded_raw == meta["rawUV"]
    assert decoded_flags == meta["flags"]


def test_golden_frames_carry_the_expected_header_metadata(
    golden: tuple[bytes, dict[str, Any]],
) -> None:
    payload, meta = golden
    frames = iter_frames(payload)

    assert len(frames) == meta["frameCount"]
    infos = [read_header(frame) for frame in frames]
    assert infos[0].seq == meta["firstSeq"]
    assert [info.seq for info in infos] == list(
        range(meta["firstSeq"], meta["firstSeq"] + len(infos))
    )
    assert all(info.boot_id == meta["bootId"] for info in infos)
    assert all(info.simulated for info in infos)
    assert sum(info.n_samples for info in infos) == meta["sampleCount"]


def test_golden_frames_exercise_the_hard_paths(
    golden: tuple[bytes, dict[str, Any]],
) -> None:
    """Un fixture que solo tenga latidos limpios no prueba gran cosa."""
    payload, meta = golden
    flags = np.array(meta["flags"], dtype=np.uint8)

    assert (flags & FLAG_LEAD_OFF).any(), "sin tramo de lead-off"
    assert (flags & FLAG_EVENT_MARKER).any(), "sin marca de síntoma"
    assert (flags & FLAG_ADC_SATURATED).any(), "sin saturación del ADC"
    assert (((flags & 0xC0) >> FLAG_SQI_SHIFT) == SQ_BAD).any(), "sin tramo no analizable"
    # Offset de continua de 120 mV: fuera del rango de un int16 en µV.
    assert max(meta["rawUV"]) > 32_767
    # Más de una corrida de flags por trama en algún lado, o el RLE no se prueba.
    assert max(f[18] for f in iter_frames(payload)) > 1  # frame[18] = runCount
