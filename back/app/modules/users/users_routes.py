import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import client_ip
from app.db.models.user import User
from app.dependencies.auth_dependencies import require_admin
from app.dependencies.common_dependencies import get_db
from app.modules.users import users_service as service
from app.modules.users.users_schemas import (
    UserAccountOut,
    UserCreateInput,
    UserCreateRequest,
    UserIdInput,
    UserListInput,
    UserUpdateEmailInput,
    UserUpdateEmailRequest,
)

router = APIRouter()


@router.get("", response_model=list[UserAccountOut])
async def list_users(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserAccountOut]:
    return await service.list_users(UserListInput(requesting_user=current_user), db)


@router.post("", response_model=UserAccountOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreateRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserAccountOut:
    return await service.create_user(
        UserCreateInput(requesting_user=current_user, data=data, ip=client_ip(request)), db
    )


@router.patch("/{user_id}", response_model=UserAccountOut)
async def update_user_email(
    user_id: uuid.UUID,
    data: UserUpdateEmailRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserAccountOut:
    return await service.update_user_email(
        UserUpdateEmailInput(requesting_user=current_user, user_id=user_id, data=data), db
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.delete_user(
        UserIdInput(requesting_user=current_user, user_id=user_id, ip=client_ip(request)), db
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/password-reset", status_code=status.HTTP_204_NO_CONTENT)
async def send_password_reset(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.send_password_reset(
        UserIdInput(requesting_user=current_user, user_id=user_id, ip=client_ip(request)), db
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
