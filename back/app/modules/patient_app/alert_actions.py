"""Acciones que los avisos de la app le piden al paciente."""

VEST_MISPLACED_KIND = "vest_misplaced"


def requires_patient_response(kind: str) -> bool:
    """Solo los avisos clínicos se responden con la bitácora."""
    return kind != VEST_MISPLACED_KIND
