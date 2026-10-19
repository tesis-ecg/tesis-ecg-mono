"""Bit 6 de `leadOffFlags`: el front-end corre con otra configuración.

`INTEGRACION.md`, pendiente 6.
"""

from collections.abc import Callable
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.push import PushMessage
from app.db.models.alert import Alert, AlertSeverity
from app.modules.ingest.ingest_service import AFE_CONFIG_ALERT_KIND
from tests.ingest_helpers import device_headers


async def _alive(client: AsyncClient, device: Any, api_key: str, lead_flags: int) -> Any:
    headers = device_headers(device, api_key, lead_flags=lead_flags)
    headers.pop("Content-Type")
    return await client.post(
        "/ingest/device-status", json={"event": "alive", "durationSeconds": 0}, headers=headers
    )


async def _alerts(db: AsyncSession, patient_id: Any) -> list[Alert]:
    rows = await db.scalars(
        select(Alert).where(Alert.patient_id == patient_id, Alert.kind == AFE_CONFIG_ALERT_KIND)
    )
    return list(rows)


async def test_afe_config_mismatch_is_a_technical_alert_without_push(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    # Es un estado: el equipo lo repite en cada POST hasta reiniciar.
    assert (await _alive(client, device, api_key, 0x40)).status_code == 200
    assert (await _alive(client, device, api_key, 0x40 | 0x20)).status_code == 200

    [alert] = await _alerts(db, patient.id)
    assert alert.severity is AlertSeverity.HIGH
    assert "configuración distinta de la pedida" in alert.message
    assert sent_pushes == []


async def test_other_lead_bits_do_not_raise_it(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    assert (await _alive(client, device, api_key, 0x3F)).status_code == 200

    assert await _alerts(db, patient.id) == []
