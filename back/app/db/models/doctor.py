from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.patient import Patient
    from app.db.models.user import User


class Doctor(TimestampMixin, Base):
    __tablename__ = "doctor"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False, unique=True
    )
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(120), nullable=True)

    user: Mapped[User] = relationship(back_populates="doctor_profile")
    patients: Mapped[list[Patient]] = relationship(back_populates="doctor")
