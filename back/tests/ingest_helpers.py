"""Helpers compartidos por los tests de ingesta."""

from datetime import UTC, datetime
from typing import Any

from app.db.models.device import Device
from app.ml.decompression import STEP_MS
from tests.frame_builder import Sample, encode_samples, synth_samples

INGEST_URL = "/ingest/ecg-frames"


#: Sentinela para pedir explícitamente que NO viaje la cabecera de hora, sin
#: confundirlo con "mandá el default". `None` ya significa otra cosa en los
#: parámetros opcionales de este helper.
OMIT = object()


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def device_headers(
    device: Device,
    api_key: str,
    *,
    uptime_ms: int = 3_600_000,
    firmware: str | None = "1.4.2",
    battery: int | None = 87,
    bridge_epoch_ms: int | object | None = None,
    sync_source: str | None = "ntp",
    sync_uncertainty_ms: int | None = 45,
    lead_flags: int | None = None,
    loss_flags: int | None = None,
    status_flags: int | None = None,
    backlog_seconds: int | None = None,
) -> dict[str, str]:
    """Cabeceras de un equipo de campo, con hora sincronizada.

    El puente de Biomédica manda las tres cabeceras de hora desde que
    `ingest_require_time_sync` está prendido, así que ese es el default acá: sin
    ellas el backend contesta `422 DEVICE_TIME_SYNC_REQUIRED` y no se probaría
    nada de lo que viene después. `bridge_epoch_ms=None` usa la hora actual, que
    es lo que manda un puente recién sincronizado; `OMIT` corta las tres para
    los tests que ejercitan el camino viejo.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Device-Serial": device.serial_number,
        "X-Device-Uptime-Ms": str(uptime_ms),
        "Content-Type": "application/octet-stream",
    }
    if bridge_epoch_ms is None:
        bridge_epoch_ms = now_ms()
    if bridge_epoch_ms is not OMIT:
        headers["X-Bridge-Epoch-Ms"] = str(bridge_epoch_ms)
        if sync_source is not None:
            headers["X-Time-Sync-Source"] = sync_source
        if sync_uncertainty_ms is not None:
            headers["X-Time-Sync-Uncertainty-Ms"] = str(sync_uncertainty_ms)
    if firmware is not None:
        headers["X-Firmware-Version"] = firmware
    if battery is not None:
        headers["X-Battery-Pct"] = str(battery)
    # Las cuatro de diagnóstico van solo cuando el test las pide: el firmware
    # anterior a septiembre de 2026 no las manda, y ese camino tiene que seguir
    # andando (`INTEGRACION.md` §11.1: "son aditivas y opcionales de leer").
    for name, value in (
        ("X-Device-Lead-Flags", lead_flags),
        ("X-Device-Loss-Flags", loss_flags),
        ("X-Device-Status-Flags", status_flags),
        ("X-Device-Backlog-Seconds", backlog_seconds),
    ):
        if value is not None:
            headers[name] = str(value)
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
    "OMIT",
    "STEP_MS",
    "Sample",
    "build_frames",
    "build_frames_with_flag_span",
    "device_headers",
    "now_ms",
    "post_frames",
]
