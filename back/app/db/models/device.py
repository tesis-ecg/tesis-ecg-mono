import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Device(TimestampMixin, Base):
    __tablename__ = "device"

    serial_number: Mapped[str] = mapped_column(String(120), unique=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient.id"), nullable=True
    )
    api_key_hash: Mapped[str] = mapped_column(String(255))
    firmware_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_battery_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sd_free_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
