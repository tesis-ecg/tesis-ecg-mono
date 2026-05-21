from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Doctor(TimestampMixin, Base):
    __tablename__ = "doctor"

    auth0_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    surname: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
