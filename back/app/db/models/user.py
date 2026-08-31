from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.doctor import Doctor
    from app.db.models.patient import Patient


class UserRole(enum.StrEnum):
    MEDICO = "medico"
    ADMIN = "admin"
    #: Cuenta de la app móvil. No tiene acceso al portal: `get_role_scope`
    #: rechaza todo rol que no sea médico o admin.
    PACIENTE = "paciente"


class IdentityStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"


class User(TimestampMixin, Base):
    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_user_email_normalized"),
        CheckConstraint(
            "pending_email IS NULL OR pending_email = lower(pending_email)",
            name="ck_user_pending_email_normalized",
        ),
    )

    auth0_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(240), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(
            IdentityStatus,
            name="identity_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=IdentityStatus.ACTIVE,
        nullable=False,
    )
    pending_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    session_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_logout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    doctor_profile: Mapped[Doctor | None] = relationship(back_populates="user")
    patient_profile: Mapped[Patient | None] = relationship(
        back_populates="user_account", foreign_keys="[Patient.user_id]"
    )
