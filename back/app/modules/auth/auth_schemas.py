import re

from pydantic import Field, field_validator

from app.db.models.user import UserRole
from app.modules._base_schema import CamelModel


class LoginRequest(CamelModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserOut(CamelModel):
    id: str
    email: str
    fullName: str
    role: UserRole


class LoginResponse(CamelModel):
    user: UserOut
    expiresAt: str


class RegisterRequest(CamelModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    fullName: str = Field(min_length=2, max_length=240)
    role: UserRole


class ForgotPasswordRequest(CamelModel):
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


class AuthErrorResponse(CamelModel):
    code: str
    message: str
