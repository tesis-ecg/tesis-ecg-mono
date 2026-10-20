"""Aviso de un chaleco que dejó de subir (`INTEGRACION.md`, pendiente 5).

Es la única alerta que nace de que NO llegue un POST: la crea un barrido
periódico y la cierra el primer contacto del equipo.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.push import PushMessage
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.study import StudyStatus
from app.modules.ingest.contact_watch import DEVICE_NO_CONTACT_ALERT_KIND
from tests.ingest_helpers import device_headers

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def cron_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cron_secret", SECRET)


async def _sweep(client: AsyncClient, secret: str = SECRET) -> Any:
    return await client.get(
        "/internal/device-contact-sweep", headers={"Authorization": f"Bearer {secret}"}
    )


async def _alerts(db: AsyncSession, patient_id: Any) -> list[Alert]:
    rows = await db.scalars(
        select(Alert).where(
            Alert.patient_id == patient_id, Alert.kind == DEVICE_NO_CONTACT_ALERT_KIND
        )
    )
    return list(rows)


async def _silent_study(
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    make_study: Callable[..., Any],
    *,
    silent: timedelta,
    status: StudyStatus = StudyStatus.IN_PROGRESS,
) -> tuple[Any, Any, str]:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient, last_seen_at=datetime.now(UTC) - silent)
    await make_study(patient, device, status=status)
    return patient, device, api_key


async def test_open_study_silent_for_more_than_two_hours_alerts_and_pushes(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    make_study: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    patient, _, _ = await _silent_study(
        make_patient, make_device, make_study, silent=timedelta(hours=2, minutes=5)
    )

    response = await _sweep(client)

    assert response.status_code == 200
    assert response.json() == {"alerted": 1}
    [alert] = await _alerts(db, patient.id)
    assert alert.severity is AlertSeverity.HIGH
    assert "sin contacto hace 2 h 5 min" in alert.message
    [(pushed_to, message)] = sent_pushes
    assert pushed_to == patient.id
    assert message.body.startswith("Tu chaleco no se conecta hace 2 horas.")
    assert message.data == {"type": "device_no_contact", "alertId": str(alert.id)}


async def test_short_silences_and_closed_studies_do_not_alert(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    make_study: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    # Un corte de menos de 2 h se recupera solo, sin perder nada.
    short, _, _ = await _silent_study(
        make_patient, make_device, make_study, silent=timedelta(minutes=100)
    )
    closed, _, _ = await _silent_study(
        make_patient,
        make_device,
        make_study,
        silent=timedelta(hours=6),
        status=StudyStatus.COMPLETED,
    )

    assert (await _sweep(client)).json() == {"alerted": 0}
    assert await _alerts(db, short.id) == []
    assert await _alerts(db, closed.id) == []
    assert sent_pushes == []


async def test_one_alert_per_silence_and_the_next_contact_resolves_it(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_device: Callable[..., Any],
    make_study: Callable[..., Any],
    sent_pushes: list[tuple[Any, PushMessage]],
) -> None:
    patient, device, api_key = await _silent_study(
        make_patient, make_device, make_study, silent=timedelta(hours=3)
    )

    await _sweep(client)
    # El barrido corre cada pocos minutos: el mismo silencio no vuelve a avisar.
    assert (await _sweep(client)).json() == {"alerted": 0}
    assert len(sent_pushes) == 1

    headers = device_headers(device, api_key)
    headers.pop("Content-Type")
    response = await client.post(
        "/ingest/device-status", json={"event": "alive", "durationSeconds": 0}, headers=headers
    )

    assert response.status_code == 200
    [alert] = await _alerts(db, patient.id)
    assert alert.acknowledged_at is not None
    assert alert.acknowledged_by is None


async def test_sweep_does_not_exist_without_the_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await _sweep(client, "otro" * 10)).status_code == 404
    assert (await client.get("/internal/device-contact-sweep")).status_code == 404
    monkeypatch.setattr(settings, "cron_secret", None)
    assert (await _sweep(client)).status_code == 404
