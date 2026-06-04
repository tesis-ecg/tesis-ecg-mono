from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.user import User
from app.dependencies.auth_dependencies import get_current_user
from app.dependencies.common_dependencies import get_db
from app.modules.auth.auth_schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    UserOut,
)
from app.modules.auth.auth_service import (
    ForgotPasswordInput,
    LoginInput,
    LogoutInput,
    RegisterInput,
    forgot_password,
    login,
    logout,
    register,
)

router = APIRouter()

_COOKIE_NAME = "session"


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="strict",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
async def login_endpoint(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    token, result = await login(
        LoginInput(email=body.email, password=body.password, ip=_client_ip(request)), db
    )
    _set_session_cookie(response, token)
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_endpoint(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await logout(LogoutInput(user=current_user, ip=_client_ip(request)), db)
    response.delete_cookie(key=_COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me_endpoint(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(
        id=str(current_user.id),
        email=current_user.email,
        fullName=current_user.full_name,
        role=current_user.role.value,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register_endpoint(
    body: RegisterRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    return await register(
        RegisterInput(data=body, requesting_user=current_user, ip=_client_ip(request)), db
    )


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password_endpoint(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    await forgot_password(ForgotPasswordInput(email=body.email, ip=_client_ip(request)), db)
