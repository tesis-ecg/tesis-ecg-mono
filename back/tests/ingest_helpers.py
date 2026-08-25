"""Helpers compartidos por los tests de ingesta."""

from typing import Any

from app.db.models.device import Device
from app.ml.decompression import STEP_MS
from tests.frame_builder import Sample, encode_samples, synth_samples

INGEST_URL = "/ingest/ecg-frames"


def device_headers(
    device: Device,
    api_key: str,
    *,
    uptime_ms: int = 3_600_000,
    firmware: str | None = "1.4.2",
    battery: int | None = 87,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Device-Serial": device.serial_number,
        "X-Device-Uptime-Ms": str(uptime_ms),
        "Content-Type": "application/octet-stream",
    }
    if firmware is not None:
        headers["X-Firmware-Version"] = firmware
    if battery is not None:
        headers["X-Battery-Pct"] = str(battery)
    return headers


def build_frames(
    n_samples: int = 900,
    *,
    first_seq: int = 0,
    boot_id: int = 0,
    t0_ms: int = 0,
    flags: int = 0,
    simulated: bool = True,
) -> list[bytes]:
    return encode_samples(
        synth_samples(n_samples, t0_ms=t0_ms, flags=flags),
        first_seq=first_seq,
        boot_id=boot_id,
        simulated=simulated,
    )


def build_frames_with_flag_span(
    n_samples: int,
    *,
    span: tuple[int, int],
    flags: int,
    first_seq: int = 0,
    boot_id: int = 0,
    simulated: bool = True,
) -> list[bytes]:
    samples = synth_samples(n_samples)
    start, end = span
    for sample in samples[start:end]:
        sample.flags = flags
    return encode_samples(samples, first_seq=first_seq, boot_id=boot_id, simulated=simulated)


async def post_frames(
    client: Any,
    device: Device,
    api_key: str,
    frames: list[bytes],
    **header_kwargs: Any,
) -> Any:
    return await client.post(
        INGEST_URL,
        content=b"".join(frames),
        headers=device_headers(device, api_key, **header_kwargs),
    )


__all__ = [
    "INGEST_URL",
    "STEP_MS",
    "Sample",
    "build_frames",
    "build_frames_with_flag_span",
    "device_headers",
    "post_frames",
]
