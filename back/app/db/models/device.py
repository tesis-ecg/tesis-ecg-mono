from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.ecg_batch import ECGBatch
    from app.db.models.patient import Patient


class DeviceStatus(enum.StrEnum):
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


class Device(TimestampMixin, Base):
    __tablename__ = "device"

    serial_number: Mapped[str] = mapped_column(String(120), unique=True)
    model: Mapped[str] = mapped_column(String(120), default="Holter ECG")
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient.id"), nullable=True
    )
    api_key_hash: Mapped[str] = mapped_column(String(255))
    firmware_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_battery_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sd_free_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(
            DeviceStatus,
            name="device_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=DeviceStatus.AVAILABLE,
        nullable=False,
    )

    patient: Mapped[Patient | None] = relationship(back_populates="devices")
    ecg_batches: Mapped[list[ECGBatch]] = relationship(back_populates="device")
