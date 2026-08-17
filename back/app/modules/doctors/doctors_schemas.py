"""Doctors schemas."""

import uuid
from dataclasses import dataclass

from app.modules._base_schema import CamelModel


class DoctorOptionOut(CamelModel):
    id: uuid.UUID
    fullName: str


@dataclass(frozen=True)
class DoctorListInput:
    # Sin filtros: el catálogo completo alimenta el Select de asignación del admin.
    pass
