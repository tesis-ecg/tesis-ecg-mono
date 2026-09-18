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
from app.db.models.study_timeline_segment import TimeSyncSource

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
        CheckConstraint("preceding_seq_gap_frames >= 0", name="ck_ecg_batch_preceding_gap"),
        CheckConstraint(
            "device_lead_flags IS NULL OR device_lead_flags BETWEEN 0 AND 255",
            name="ck_ecg_batch_lead_flags",
        ),
        CheckConstraint(
            "device_loss_flags IS NULL OR device_loss_flags BETWEEN 0 AND 255",
            name="ck_ecg_batch_loss_flags",
        ),
        CheckConstraint(
            "device_status_flags IS NULL OR device_status_flags BETWEEN 0 AND 255",
            name="ck_ecg_batch_status_flags",
        ),
        CheckConstraint(
            "device_backlog_seconds IS NULL OR device_backlog_seconds BETWEEN 0 AND 65535",
            name="ck_ecg_batch_backlog_seconds",
        ),
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
    #: Ancla efectivamente usada para este lote: el instante UTC en que el
    #: `millis()` del equipo valía cero. Se guarda el ancla, no solo la hora
    #: derivada: si más adelante resulta que estaba mal, con el crudo se puede
    #: recalcular todo (INTEGRACION.md §5).
    epoch_anchor_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Epoch UTC que mandó el puente WiFi en `X-Bridge-Epoch-Ms`, leído del otro
    #: lado del enlace en el mismo instante que `device_uptime_ms`. `NULL` en
    #: los lotes anteriores a `docs/integracion-ingesta-con-horario.md`, donde el
    #: ancla se derivaba de nuestra hora de recepción y arrastraba la latencia
    #: del pedido. Es una muestra de ancla: la corrección de deriva del tramo se
    #: ajusta sobre todas las del mismo arranque.
    bridge_epoch_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    time_sync_source: Mapped[TimeSyncSource] = mapped_column(
        Enum(
            TimeSyncSource,
            name="time_sync_source",
            values_callable=lambda e: [v.value for v in e],
        ),
        default=TimeSyncSource.SERVER_RECEIVE,
        nullable=False,
    )
    #: Lo que el puente declara que puede estar errada su hora. No rechaza nada;
    #: pesa las anclas en el ajuste y le dice al médico cuánto vale la hora.
    time_sync_uncertainty_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    frames_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frames_duplicate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Cuántas tramas faltan entre el cursor del estudio y la primera de este
    #: lote (`INTEGRACION.md` §4.6).
    #:
    #: Distinto de cero significa **pérdida real de señal del paciente**: el
    #: equipo saltó hacia adelante porque su log circular dio la vuelta y esas
    #: tramas ya no existen en ningún lado. Se guarda acá, en la ruta del ACK,
    #: porque es el único momento en que se conoce: el procesamiento ve el lote
    #: aislado y no sabe contra qué cursor entró.
    preceding_seq_gap_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Diagnóstico del paquete de STATUS que traía el POST de este lote
    #: (`INTEGRACION.md` §11.1), acumulado con OR desde el último POST
    #: confirmado. `NULL` = firmware anterior a septiembre de 2026.
    device_lead_flags: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    device_loss_flags: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    device_status_flags: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: Estimación de atraso del propio firmware. Se archiva para diagnóstico y
    #: **no se usa para alertar**: va ~2× alto (ver `DeviceContext.backlog_seconds`).
    device_backlog_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Las 256 B por trama tal cual llegaron. Fuente de verdad verificable por
    #: CRC: siempre se puede volver al byte exacto que grabó el equipo.
    frames_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="ecg_batches")
    study: Mapped["Study | None"] = relationship()
    events: Mapped[list["ECGEvent"]] = relationship(back_populates="batch")
