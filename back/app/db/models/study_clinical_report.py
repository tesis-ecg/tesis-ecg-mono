from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StudyClinicalReportDraft(TimestampMixin, Base):
    """El único borrador editable de informe para un estudio."""

    __tablename__ = "study_clinical_report_draft"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_study_report_draft_revision"),)

    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    indication: Mapped[str | None] = mapped_column(Text, nullable=True)
    medications: Mapped[str | None] = mapped_column(Text, nullable=True)
    referring_professional: Mapped[str | None] = mapped_column(String(240), nullable=True)
    technician: Mapped[str | None] = mapped_column(String(240), nullable=True)
    clinical_observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )


class StudyClinicalReport(TimestampMixin, Base):
    """Versión final inmutable del informe, incluido su PDF verificable."""

    __tablename__ = "study_clinical_report"
    __table_args__ = (
        UniqueConstraint("study_id", "version", name="uq_study_report_version"),
        CheckConstraint("version >= 1", name="ck_study_report_version"),
        CheckConstraint("pdf_byte_length > 0", name="ck_study_report_pdf_length"),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("study.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pdf_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pdf_byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finalized_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
