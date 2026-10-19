"""Acciones que los avisos de la app le piden al paciente."""

VEST_MISPLACED_KIND = "vest_misplaced"

#: Los avisos que no se responden con la bitácora: se resuelven con una acción
#: sobre el equipo (acomodarlo, cargarlo, acercarlo al WiFi) o son técnicos.
NO_RESPONSE_KINDS = frozenset(
    {
        VEST_MISPLACED_KIND,
        "battery_low",
        "battery_critical",
        "device_no_contact",
        "afe_config_mismatch",
    }
)


def requires_patient_response(kind: str) -> bool:
    """Solo los avisos clínicos se responden con la bitácora."""
    return kind not in NO_RESPONSE_KINDS
