from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.alert import Alert
    from app.db.models.device import Device
    from app.db.models.doctor import Doctor
    from app.db.models.user import User


class PatientSex(enum.StrEnum):
    M = "M"
    F = "F"
    X = "X"


class PatientStudyStatus(enum.StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    NONE = "none"


class Patient(TimestampMixin, Base):
    __tablename__ = "patient"

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctor.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    medical_record_num: Mapped[str] = mapped_column(String(120), unique=True)
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120))
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    dni: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sex: Mapped[PatientSex] = mapped_column(
        Enum(PatientSex, name="patient_sex", values_callable=lambda obj: [e.value for e in obj]),
        default=PatientSex.X,
        nullable=False,
    )
    study_status: Mapped[PatientStudyStatus] = mapped_column(
        Enum(
            PatientStudyStatus,
            name="patient_study_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=PatientStudyStatus.NONE,
        nullable=False,
    )
    last_data_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    doctor: Mapped[Doctor] = relationship(back_populates="patients")
    user_account: Mapped[User | None] = relationship(
        back_populates="patient_profile", foreign_keys=[user_id]
    )
    devices: Mapped[list[Device]] = relationship(back_populates="patient")
    alerts: Mapped[list[Alert]] = relationship(back_populates="patient")
