from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from inferia.services.api_gateway.db.database import get_db
from inferia.services.api_gateway.db.models import User as DBUser, UserOrganization
from inferia.services.api_gateway.schemas.management import UserCreate, UserResponse
from inferia.services.api_gateway.schemas.auth import PermissionEnum
from inferia.services.api_gateway.management.dependencies import get_current_user_context
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.rbac.authorization import authz_service

router = APIRouter(tags=["Users"])


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    user_data: UserCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MEMBER_INVITE)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )
    existing = await db.execute(select(DBUser).where(DBUser.email == user_data.email))
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="User already exists")

    hashed_pw = auth_service.get_password_hash(user_data.password)
    new_user = DBUser(
        email=user_data.email, password_hash=hashed_pw, default_org_id=user_ctx.org_id
    )

    db.add(new_user)
    await db.flush()

    uo = UserOrganization(
        user_id=new_user.id, org_id=user_ctx.org_id, role=user_data.role
    )
    db.add(uo)

    await db.commit()
    await db.refresh(new_user)

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        role=user_data.role,
        org_id=user_ctx.org_id,
        created_at=new_user.created_at,
    )


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of users to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of users to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """List users with pagination."""
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MEMBER_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    stmt = (
        select(DBUser, UserOrganization.role)
        .join(UserOrganization, DBUser.id == UserOrganization.user_id)
        .where(UserOrganization.org_id == user_ctx.org_id)
        .offset(skip)
        .limit(limit)
    )
    users_result = await db.execute(stmt)

    users = []
    for user, role in users_result.all():
        users.append(
            UserResponse(
                id=user.id,
                email=user.email,
                role=role,
                org_id=user_ctx.org_id,
                created_at=user.created_at,
            )
        )

    return users
