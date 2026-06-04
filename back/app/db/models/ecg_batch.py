import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.device import Device
    from app.db.models.ecg_event import ECGEvent


class ProcessingStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class ECGBatch(TimestampMixin, Base):
    __tablename__ = "ecg_batch"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device.id"), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    batch_timestamp: Mapped[int] = mapped_column(BigInteger)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer)
    num_channels: Mapped[int] = mapped_column(Integer, default=3)
    num_samples: Mapped[int] = mapped_column(Integer)
    compression_type: Mapped[str] = mapped_column(String(50))
    s3_key: Mapped[str] = mapped_column(String(1024))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status"),
        default=ProcessingStatus.PENDING,
    )
    processing_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(120), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="ecg_batches")
    events: Mapped[list["ECGEvent"]] = relationship(back_populates="batch")
