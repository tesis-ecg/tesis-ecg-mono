from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.device import Device
    from app.db.models.patient import Patient


class StudyStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SCHEDULED = "scheduled"


class Study(TimestampMixin, Base):
    __tablename__ = "study"
    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_study_time_range"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_study_duration"),
        CheckConstraint("samples_count >= 0", name="ck_study_samples_count"),
        CheckConstraint("events_count >= 0", name="ck_study_events_count"),
        CheckConstraint("sample_rate > 0", name="ck_study_sample_rate"),
        Index("ix_study_patient_started", "patient_id", "started_at"),
        Index("ix_study_device_id", "device_id"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient.id"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[StudyStatus] = mapped_column(
        Enum(StudyStatus, name="study_status", values_callable=lambda obj: [e.value for e in obj]),
        default=StudyStatus.SCHEDULED,
        nullable=False,
    )
    samples_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ecg_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    ecg_encoding: Mapped[str] = mapped_column(String(32), default="float32-le", nullable=False)
    ecg_byte_length: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ecg_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ecg_pyramid_levels: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    sample_rate: Mapped[int] = mapped_column(Integer, default=250, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    patient: Mapped[Patient] = relationship()
    device: Mapped[Device] = relationship()
