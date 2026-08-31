from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.alert import Alert
    from app.db.models.patient import Patient
    from app.db.models.study import Study


class PatientReportSource(enum.StrEnum):
    #: Respuesta al formulario que abre una notificación push.
    PUSH_RESPONSE = "push_response"
    #: El paciente lo cargó por su cuenta, sin push previo.
    MANUAL = "manual"


class PatientReport(TimestampMixin, Base):
    """Un registro de la bitácora del paciente: qué sintió y qué estaba haciendo.

    Guarda **hora de pared** (``occurred_at``) y no un offset en muestras, a
    diferencia de `ECGEvent`. El chaleco sube cada ~1 h: el paciente puede
    marcar un síntoma a las 14:30 y esa señal recién existir a las 15:00. La
    conversión a coordenadas del gráfico se hace al leer el manifest, cuando ya
    se sabe cuánta señal hay grabada.
    """

    __tablename__ = "patient_report"
    __table_args__ = (
        Index("ix_patient_report_patient_occurred", "patient_id", "occurred_at"),
        Index("ix_patient_report_study_occurred", "study_id", "occurred_at"),
        # Idempotencia de la bitácora: responder dos veces la misma alerta
        # actualiza la fila, no crea una segunda. Un doble tap sobre la
        # notificación no puede duplicar el registro clínico.
        Index(
            "uq_patient_report_alert",
            "alert_id",
            unique=True,
            postgresql_where=text("alert_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patient.id"), nullable=False
    )
    #: Estudio al que cae el registro. Nullable a propósito: un registro cargado
    #: sin estudio abierto es válido y vive solo en el historial del paciente.
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id"), nullable=True
    )
    #: La alerta que disparó el push, si el registro es una respuesta.
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert.id"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[PatientReportSource] = mapped_column(
        Enum(
            PatientReportSource,
            name="patient_report_source",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    #: Slugs del catálogo de síntomas (`patient_app/catalogs.py`).
    symptoms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    symptoms_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Slug del catálogo de actividad.
    activity: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    patient: Mapped[Patient] = relationship()
    study: Mapped[Study | None] = relationship()
    alert: Mapped[Alert | None] = relationship()
