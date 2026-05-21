from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import Enum, Float, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ECGEventType(enum.StrEnum):
    TACHYCARDIA = "TACHYCARDIA"
    BRADYCARDIA = "BRADYCARDIA"
    AFIB = "AFIB"
    PVC = "PVC"
    PAUSE = "PAUSE"
    NOISE = "NOISE"
    OTHER = "OTHER"


class ECGEventSeverity(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ECGEvent(TimestampMixin, Base):
    __tablename__ = "ecg_event"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ecg_batch.id"), nullable=False
    )
    event_type: Mapped[ECGEventType] = mapped_column(Enum(ECGEventType, name="ecg_event_type"))
    severity: Mapped[ECGEventSeverity] = mapped_column(
        Enum(ECGEventSeverity, name="ecg_event_severity")
    )
    timestamp_in_recording: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
