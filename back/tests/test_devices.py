import uuid
from datetime import UTC, datetime

from app.db.models.device import Device, DeviceStatus
from app.modules.devices.devices_service import holter_health_out


def test_holter_health_does_not_invent_missing_telemetry() -> None:
    device = Device(
        id=uuid.uuid4(),
        serial_number="TEST-001",
        model="Holter ECG",
        api_key_hash="hash",
        status=DeviceStatus.AVAILABLE,
        last_seen_at=datetime.now(UTC),
        last_battery_pct=None,
        last_sd_free_mb=None,
    )

    health = holter_health_out(device)

    assert health.batteryPercent is None
    assert health.signalDbm is None
    assert health.nextScheduledUploadAt is None
    assert health.uploadsToday is None
    assert health.storageTotalMb is None
    assert health.telemetryAvailable is False
    assert health.signalQuality is None
