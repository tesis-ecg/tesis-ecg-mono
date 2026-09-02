from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
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
    __table_args__ = (
        CheckConstraint(
            "(status = 'assigned' AND patient_id IS NOT NULL AND doctor_id IS NOT NULL) "
            "OR (status <> 'assigned' AND patient_id IS NULL)",
            name="ck_device_assignment_state",
        ),
        CheckConstraint(
            "last_battery_pct IS NULL OR last_battery_pct BETWEEN 0 AND 100",
            name="ck_device_battery_pct",
        ),
        CheckConstraint(
            "last_sd_free_mb IS NULL OR last_sd_free_mb >= 0",
            name="ck_device_sd_free_nonnegative",
        ),
        Index(
            "uq_device_active_patient",
            "patient_id",
            unique=True,
            postgresql_where=text("patient_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    serial_number: Mapped[str] = mapped_column(String(120), unique=True)
    model: Mapped[str] = mapped_column(String(120), default="Holter ECG")
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient.id"), nullable=True
    )
    # Ownership device→médico (lo setea el admin). Sin relationship(): el repo no usa
    # lazy loading y en async una relación no cargada tira MissingGreenlet.
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctor.id"), nullable=True, index=True
    )
    api_key_hash: Mapped[str] = mapped_column(String(255))
    firmware_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_battery_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sd_free_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Última colocación reportada por el equipo (`POST /ingest/device-status`).
    #: `None` no es "está bien": es que todavía no reportó ninguna de las dos
    #: cosas. La app lo dibuja como estado desconocido y no como correcto.
    placement_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    placement_reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
