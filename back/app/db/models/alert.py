from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.doctor import Doctor
    from app.db.models.ecg_event import ECGEvent
    from app.db.models.patient import Patient


class AlertSeverity(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Alert(TimestampMixin, Base):
    __tablename__ = "alert"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ecg_event.id"), nullable=False
    )
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity, name="alert_severity"))
    message: Mapped[str] = mapped_column(String(1024))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctor.id"), nullable=True
    )

    patient: Mapped[Patient] = relationship(back_populates="alerts")
    event: Mapped[ECGEvent] = relationship(back_populates="alerts")
    acknowledged_by_doctor: Mapped[Doctor | None] = relationship()
