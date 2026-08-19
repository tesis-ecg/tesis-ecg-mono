from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limit"
    __table_args__ = (Index("ix_auth_rate_limit_bucket_start", "bucket_start"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
