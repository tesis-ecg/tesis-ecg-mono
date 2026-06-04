import enum
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AuditEventType(enum.StrEnum):
    LOGIN_OK = "LOGIN_OK"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    REGISTER = "REGISTER"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_event"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, name="audit_event_type"), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
