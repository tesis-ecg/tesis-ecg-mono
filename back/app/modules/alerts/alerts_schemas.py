"""Alerts schemas."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.modules._base_schema import CamelModel


class AlertOut(CamelModel):
    """Una alerta real de la tabla `alert`.

    Diferencia con `DashboardAlertOut`: ese DTO mezcla estas alertas con las
    sintéticas de "equipo sin transmitir", que no existen como fila y por lo
    tanto no se pueden acusar. Acá solo viven las acusables, y por eso `id` es
    un UUID de verdad y no un string con prefijo.
    """

    id: uuid.UUID
    patientId: uuid.UUID
    patientName: str
    #: Minúsculas, igual que en el dashboard: el ORM las guarda en MAYÚSCULAS.
    kind: str
    severity: str
    message: str
    detectedAt: datetime
    studyId: uuid.UUID | None
    seenAt: datetime | None
    acknowledgedAt: datetime | None
    acknowledgedByName: str | None


class AlertListResponse(CamelModel):
    items: list[AlertOut]
    total: int
    limit: int
    offset: int
    #: Pendientes en total, sin importar el filtro ni la página. Es lo que
    #: alimenta el badge del menú, que tiene que contar todo y no la página.
    pendingTotal: int


@dataclass(frozen=True)
class AlertListInput:
    doctor_id: uuid.UUID | None
    #: `None` = todas; `True` = solo acusadas; `False` = solo pendientes.
    acknowledged: bool | None
    severity: list[str] | None
    limit: int
    offset: int


@dataclass(frozen=True)
class AlertIdInput:
    doctor_id: uuid.UUID | None
    alert_id: uuid.UUID
    actor_id: uuid.UUID | None = None
