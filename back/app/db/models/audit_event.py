import enum
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AuditEventType(enum.StrEnum):
    LOGIN_OK = "LOGIN_OK"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    REGISTER = "REGISTER"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"  # nosec B105
    USER_UPDATED = "USER_UPDATED"
    USER_DELETED = "USER_DELETED"
    PATIENT_CREATED = "PATIENT_CREATED"
    PATIENT_UPDATED = "PATIENT_UPDATED"
    PATIENT_DELETED = "PATIENT_DELETED"
    DEVICE_CREATED = "DEVICE_CREATED"
    DEVICE_UPDATED = "DEVICE_UPDATED"
    DEVICE_RETIRED = "DEVICE_RETIRED"
    DEVICE_ASSIGNED = "DEVICE_ASSIGNED"
    DEVICE_API_KEY_ROTATED = "DEVICE_API_KEY_ROTATED"
    STUDY_COMPLETED = "STUDY_COMPLETED"
    STUDY_CANCELLED = "STUDY_CANCELLED"
    ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED"
    ECG_ACCESSED = "ECG_ACCESSED"


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, name="audit_event_type"), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
