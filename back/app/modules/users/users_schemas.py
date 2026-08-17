"""Users schemas."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import Field

from app.db.models.user import User, UserRole
from app.modules._base_schema import CamelModel


class UserAccountOut(CamelModel):
    id: uuid.UUID
    email: str
    fullName: str
    role: UserRole
    isActive: bool
    createdAt: datetime


class UserCreateRequest(CamelModel):
    fullName: str = Field(min_length=1, max_length=240)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8)
    role: UserRole


class UserUpdateEmailRequest(CamelModel):
    email: str = Field(min_length=3, max_length=320)


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
