"""Tipo de alerta, resuelto en un solo lugar.

Antes vivía duplicado en `alerts_service` y `dashboard_service`. Se unificó
cuando `alert.event_id` pasó a ser nullable: con dos copias, la alerta de
"chaleco mal colocado" —que no cuelga de ningún `ecg_event`— habría quedado
resuelta distinto en la bandeja y en el dashboard.

Las severidades y los tipos viajan en minúsculas por la API; el ORM los guarda
en MAYÚSCULAS porque esos Enum no llevan `values_callable`.
"""

from typing import Any

from app.db.models.ecg_event import ECGEventType

FALLBACK = "other"


def resolve_alert_kind(
    alert_kind: str | None,
    event_type: ECGEventType | None,
    event_metadata: dict[str, Any] | None,
) -> str:
    """`alert.kind` manda; si no está, se deriva del evento que la disparó."""
    if alert_kind:
        return alert_kind
    if event_type is None:
        return FALLBACK
    metadata = event_metadata or {}
    if metadata.get("kind") == "symptom_marker":
        return "symptom_marker"
    if event_type == ECGEventType.OTHER:
        return FALLBACK
    return event_type.value.lower()
