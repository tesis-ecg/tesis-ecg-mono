from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.doctor import Doctor
    from app.db.models.patient import Patient


class UserRole(enum.StrEnum):
    MEDICO = "medico"
    PACIENTE = "paciente"
    ADMIN = "admin"
    INVESTIGADOR = "investigador"
    ASISTENTE = "asistente"


class User(TimestampMixin, Base):
    __tablename__ = "user"

    auth0_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(240), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_logout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    doctor_profile: Mapped[Doctor | None] = relationship(back_populates="user")
    patient_profile: Mapped[Patient | None] = relationship(
        back_populates="user_account", foreign_keys="[Patient.user_id]"
    )
