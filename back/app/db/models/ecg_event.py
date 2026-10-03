from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.alert import Alert
    from app.db.models.ecg_batch import ECGBatch
    from app.db.models.study import Study


class ECGEventType(enum.StrEnum):
    TACHYCARDIA = "TACHYCARDIA"
    BRADYCARDIA = "BRADYCARDIA"
    AFIB = "AFIB"
    PVC = "PVC"
    PAUSE = "PAUSE"
    NOISE = "NOISE"
    #: Morfología atípica sin etiqueta de enfermedad. Es lo único que el motor no
    #: supervisado puede afirmar: "este latido no se parece a los tuyos". Llamarlo
    #: `PVC` sería un diagnóstico que ningún dato del sistema respalda.
    ANOMALY = "ANOMALY"
    OTHER = "OTHER"


class ECGEventSeverity(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ECGEventValidation(enum.StrEnum):
    """Veredicto del médico sobre un hallazgo del motor.

    Se persiste desde el día uno aunque la UI de validación llegue después: cada
    "descartado" es una etiqueta de ruido revisada por un especialista, y son las
    únicas etiquetas de NUESTRO hardware que el proyecto va a tener. Sin la
    columna se pierden meses de ellas.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class ECGEvent(TimestampMixin, Base):
    __tablename__ = "ecg_event"
    __table_args__ = (
        CheckConstraint("timestamp_in_recording >= 0", name="ck_ecg_event_timestamp"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_ecg_event_duration",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1",
            name="ck_ecg_event_confidence",
        ),
        CheckConstraint(
            "validated_at IS NULL OR validated_by IS NOT NULL",
            name="ck_ecg_event_validation_actor",
        ),
        Index("ix_ecg_event_study_ts", "study_id", "timestamp_in_recording"),
        Index("ix_ecg_event_batch", "batch_id"),
        Index(
            "uq_ecg_event_dedupe",
            "study_id",
            "dedupe_key",
            unique=True,
            postgresql_where="dedupe_key IS NOT NULL AND deleted_at IS NULL",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ecg_batch.id"), nullable=False
    )
    #: Camino directo al estudio. Nullable a propósito: `ecg_batch.study_id`
    #: también lo es (los lotes de `seed_demo` no cuelgan de un estudio), así que
    #: el backfill de la migración puede dejar filas sin resolver y un NOT NULL
    #: haría fallar `alembic upgrade head` sobre cualquier base con datos demo.
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id"), nullable=True
    )
    event_type: Mapped[ECGEventType] = mapped_column(Enum(ECGEventType, name="ecg_event_type"))
    severity: Mapped[ECGEventSeverity] = mapped_column(
        Enum(ECGEventSeverity, name="ecg_event_severity")
    )
    timestamp_in_recording: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Cuán atípico es el hallazgo, en [0, 1]. **No** es una probabilidad
    #: calibrada y no hay con qué calibrarla: el motor es no supervisado. Las
    #: componentes crudas (prematurity, dissimilarity, bsqi) viajan en
    #: `event_metadata`, donde son consultables con `metadata ->> '...'`.
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    # --- Trazabilidad del motor ---------------------------------------------- #
    #: Versión del pipeline que produjo la fila. **Es el único predicado que
    #: define "esto lo escribió el motor y se puede reescribir"**: los hallazgos
    #: manuales de `simulate-anomaly`, la Capa A y los seeds legacy lo tienen en
    #: NULL y el motor nunca los toca (ni al upsertear encabezados ni al fundir
    #: morfologías en el cierre).
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Clave natural del hallazgo dentro del estudio (`kind:startSample`,
    #: `cluster:3`). Es la clave del upsert de los encabezados por morfología y
    #: la red de seguridad contra escribir dos veces el mismo episodio: el
    #: reintento de un lote que falló no la necesita, porque el rollback ya se
    #: llevó todo.
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validation_status: Mapped[ECGEventValidation] = mapped_column(
        Enum(
            ECGEventValidation,
            name="ecg_event_validation",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=ECGEventValidation.PENDING,
        server_default=ECGEventValidation.PENDING.value,
        nullable=False,
    )
    validated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctor.id"), nullable=True
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    batch: Mapped[ECGBatch] = relationship(back_populates="events")
    study: Mapped[Study | None] = relationship()
    alerts: Mapped[list[Alert]] = relationship(back_populates="event")
