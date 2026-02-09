from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from db.database import get_db
from db.models import User, Role, UserOrganization

from models import (
    UserResponse as PydanticUserResponse,
    PermissionEnum,
)  # existing in models.py
from .middleware import get_current_user_from_request
from .authorization import authz_service

from pydantic import BaseModel

router = APIRouter(prefix="/admin/users", tags=["User Management"])


class UserRoleUpdate(BaseModel):
    role: str


@router.get("", response_model=List[PydanticUserResponse])
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of users to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of users to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all users in the current organization with pagination."""
    user_ctx = get_current_user_from_request(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MEMBER_LIST)

    stmt = (
        select(User, UserOrganization.role)
        .join(UserOrganization, User.id == UserOrganization.user_id)
        .where(UserOrganization.org_id == user_ctx.org_id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    users_data = result.all()

    return [
        PydanticUserResponse(
            id=u.id,
            email=u.email,
            role=role,
            org_id=user_ctx.org_id,
            created_at=u.created_at,
        )
        for u, role in users_data
    ]


@router.put("/{user_id}/role")
async def update_user_role(
    user_id: str,
    role_update: UserRoleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Assign a role to a user."""
    user_ctx = get_current_user_from_request(request)
    authz_service.require_permission(user_ctx, PermissionEnum.ROLE_UPDATE)

    # Verify role exists
    role_check = await db.execute(select(Role).where(Role.name == role_update.role))
    if not role_check.scalars().first():
        raise HTTPException(
            status_code=400, detail=f"Role '{role_update.role}' does not exist"
        )

    # Get UserOrganization link for the current admin's org (which is user_ctx.org_id)
    stmt = select(UserOrganization).where(
        UserOrganization.user_id == user_id, UserOrganization.org_id == user_ctx.org_id
    )
    result = await db.execute(stmt)
    user_org = result.scalars().first()

    if not user_org:
        raise HTTPException(
            status_code=404, detail="User not found in this organization"
        )

    user_org.role = role_update.role
    await db.commit()

    return {"message": "User role updated"}
