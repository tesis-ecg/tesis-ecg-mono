from app.db.models.user import UserRole
from app.modules._base_schema import CamelModel


class LoginRequest(CamelModel):
    email: str
    password: str


class UserOut(CamelModel):
    id: str
    email: str
    fullName: str
    role: str


class LoginResponse(CamelModel):
    user: UserOut
    expiresAt: str


class RegisterRequest(CamelModel):
    email: str
    password: str
    fullName: str
    role: UserRole


class ForgotPasswordRequest(CamelModel):
    email: str


class AuthErrorResponse(CamelModel):
    code: str
    message: str
