import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.device import Device
    from app.db.models.ecg_event import ECGEvent
    from app.db.models.study import Study


class ProcessingStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class ECGBatch(TimestampMixin, Base):
    __tablename__ = "ecg_batch"
    __table_args__ = (
        CheckConstraint("duration_seconds >= 0", name="ck_ecg_batch_duration"),
        CheckConstraint("sample_rate > 0", name="ck_ecg_batch_sample_rate"),
        CheckConstraint("num_channels > 0", name="ck_ecg_batch_channels"),
        CheckConstraint("num_samples >= 0", name="ck_ecg_batch_samples"),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_ecg_batch_file_size",
        ),
        CheckConstraint(
            "boot_id IS NULL OR boot_id BETWEEN 0 AND 15",
            name="ck_ecg_batch_boot_id",
        ),
        CheckConstraint("frames_count >= 0", name="ck_ecg_batch_frames_count"),
        CheckConstraint("frames_rejected >= 0", name="ck_ecg_batch_frames_rejected"),
        CheckConstraint("frames_duplicate >= 0", name="ck_ecg_batch_frames_duplicate"),
        Index("ix_ecg_batch_study_first_seq", "study_id", "first_seq"),
    )

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

    # --- Ingesta desde el chaleco --------------------------------------------- #
    #: Nullable por compatibilidad: los batches que escribe `seed_demo` no
    #: cuelgan de un estudio. Todo batch ingestado sí lo tiene.
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id"), nullable=True
    )
    #: Contador de arranque del equipo (0-15). Un cambio entre `seq` consecutivos
    #: significa "el equipo se reinició acá y `t0Ms` volvió a cero".
    boot_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: `millis()` del equipo al momento de enviar. Es la mitad del ancla temporal.
    device_uptime_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: `received_at_ms - device_uptime_ms`. Se guarda el ancla usada, no solo la
    #: hora derivada: si más adelante resulta que estaba mal, con el crudo se
    #: puede recalcular todo (INTEGRACION.md §5).
    epoch_anchor_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    first_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    frames_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_duplicate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Las 256 B por trama tal cual llegaron. Fuente de verdad verificable por
    #: CRC: siempre se puede volver al byte exacto que grabó el equipo.
    frames_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="ecg_batches")
    study: Mapped["Study | None"] = relationship()
    events: Mapped[list["ECGEvent"]] = relationship(back_populates="batch")
