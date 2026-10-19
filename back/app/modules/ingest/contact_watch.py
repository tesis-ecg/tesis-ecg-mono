"""Aviso de un chaleco que dejó de subir.

Todas las demás alertas nacen de un POST que llega. Esta es la única que nace
de que NO llegue nada, así que no puede vivir en la ingesta: la corre un barrido
periódico (`GET /internal/device-contact-sweep`) y la cierra el primer contacto
del equipo (`resolve_contact_alerts`, desde la ingesta y desde el latido).

El equipo sabe que no sube (bit 7 de `statusFlags`, `INTEGRACION.md` §3.1), pero
ese bit viaja en el mismo POST que no sale: llega cuando la red ya volvió
(`INTEGRACION.md`, pendiente 5).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import Device
from app.db.models.study import Study, StudyStatus

#: Kind de la alerta de equipo sin contacto.
DEVICE_NO_CONTACT_ALERT_KIND = "device_no_contact"


@dataclass(frozen=True)
class SilentDevice:
    """Una alerta recién creada, con lo que necesita el push al paciente."""

    alert_id: uuid.UUID
    patient_id: uuid.UUID
    silent_minutes: int


async def sweep_silent_devices(db: AsyncSession, now: datetime) -> list[SilentDevice]:
    """Alerta cada estudio abierto cuyo equipo lleva más del umbral sin contacto.

    Una alerta por episodio de silencio: si ya hay una creada después del último
    contacto, ese silencio ya se avisó. Es el mismo criterio que el aviso de
    colocación (se absorbe la repetición dentro del episodio, no el episodio
    siguiente), y por eso no hace falta una ventana de debounce aparte.
    """
    threshold = now - timedelta(minutes=settings.device_no_contact_alert_minutes)
    rows = await db.execute(
        select(Study, Device)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.status == StudyStatus.IN_PROGRESS,
            Study.deleted_at.is_(None),
            Device.deleted_at.is_(None),
            Device.last_seen_at.is_not(None),
            Device.last_seen_at < threshold,
        )
    )
    created: list[SilentDevice] = []
    for study, device in rows.tuples():
        last_seen = device.last_seen_at
        assert last_seen is not None  # filtrado arriba; es para el tipo
        already = await db.scalar(
            select(Alert.id)
            .where(
                Alert.patient_id == study.patient_id,
                Alert.kind == DEVICE_NO_CONTACT_ALERT_KIND,
                Alert.deleted_at.is_(None),
                Alert.created_at > last_seen,
            )
            .limit(1)
        )
        if already is not None:
            continue
        silent_minutes = int((now - last_seen).total_seconds() // 60)
        alert = Alert(
            patient_id=study.patient_id,
            event_id=None,
            kind=DEVICE_NO_CONTACT_ALERT_KIND,
            severity=AlertSeverity.HIGH,
            message=(
                f"{device.serial_number}: sin contacto hace {_hours_text(silent_minutes)}. "
                "Sigue grabando en su memoria; con el chaleco flojo el registro empieza a "
                "pisar señal sin subir a las ~5 h del último envío."
            ),
        )
        db.add(alert)
        await db.flush()
        created.append(SilentDevice(alert.id, study.patient_id, silent_minutes))
    return created


async def resolve_contact_alerts(db: AsyncSession, patient_id: uuid.UUID, now: datetime) -> None:
    """El primer contacto del equipo cierra el aviso abierto de ese paciente.

    Se cierra como acusada sin médico (`acknowledged_by` vacío): nadie la atendió,
    se resolvió sola cuando el equipo volvió a subir.
    """
    alerts = await db.scalars(
        select(Alert).where(
            Alert.patient_id == patient_id,
            Alert.kind == DEVICE_NO_CONTACT_ALERT_KIND,
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
        )
    )
    for alert in alerts:
        alert.acknowledged_at = now


def _hours_text(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"
