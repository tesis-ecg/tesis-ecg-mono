"""Users schemas."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import Field, field_validator

from app.db.models.user import IdentityStatus, User, UserRole
from app.modules._base_schema import CamelModel


class UserAccountOut(CamelModel):
    id: uuid.UUID
    email: str
    fullName: str
    role: UserRole
    isActive: bool
    identityStatus: IdentityStatus
    createdAt: datetime


class UserCreateRequest(CamelModel):
    fullName: str = Field(min_length=1, max_length=240)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserUpdateEmailRequest(CamelModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Email inválido")
    return normalized


@dataclass(frozen=True)
class UserListInput:
    requesting_user: User


@dataclass(frozen=True)
class UserCreateInput:
    requesting_user: User
    data: UserCreateRequest
    ip: str | None


@dataclass(frozen=True)
class UserUpdateEmailInput:
    requesting_user: User
    user_id: uuid.UUID
    data: UserUpdateEmailRequest


@dataclass(frozen=True)
class UserIdInput:
    requesting_user: User
    user_id: uuid.UUID
    ip: str | None
