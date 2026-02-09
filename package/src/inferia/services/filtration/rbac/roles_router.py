from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from db.database import get_db
from db.models import Role
from models import PermissionEnum
from .api_models import RoleCreate, RoleUpdate, RoleResponse, PermissionResponse
from .middleware import get_current_user_from_request
from .authorization import authz_service

router = APIRouter(prefix="/admin/roles", tags=["RBAC Management"])


@router.get("", response_model=List[RoleResponse])
async def list_roles(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of roles to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of roles to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all available roles with pagination."""
    user_ctx = get_current_user_from_request(request)
    authz_service.require_permission(user_ctx, PermissionEnum.ROLE_LIST)

    result = await db.execute(select(Role).offset(skip).limit(limit))
    return result.scalars().all()


@router.post("", response_model=RoleResponse)
async def create_role(
    role_in: RoleCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    """Create a new role."""
    user_ctx = get_current_user_from_request(request)
    authz_service.require_permission(user_ctx, PermissionEnum.ROLE_CREATE)

    # Check if exists
    result = await db.execute(select(Role).where(Role.name == role_in.name))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Role already exists")

    role = Role(
        name=role_in.name,
        description=role_in.description,
        permissions=role_in.permissions,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.put("/{name}", response_model=RoleResponse)
async def update_role(
    name: str, role_in: RoleUpdate, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update a role."""
    user_ctx = get_current_user_from_request(request)
    authz_service.require_permission(user_ctx, PermissionEnum.ROLE_UPDATE)

    result = await db.execute(select(Role).where(Role.name == name))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if role_in.description is not None:
        role.description = role_in.description
    if role_in.permissions is not None:
        # Unique permissions
        role.permissions = list(set(role_in.permissions))

    await db.commit()
    await db.refresh(role)
    return role


@router.delete("/{name}")
async def delete_role(name: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a role."""
    user_ctx = get_current_user_from_request(request)
    authz_service.require_permission(user_ctx, PermissionEnum.ROLE_DELETE)

    result = await db.execute(select(Role).where(Role.name == name))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if name in ["admin", "member"]:
        raise HTTPException(
            status_code=400, detail="Cannot delete default system roles"
        )

    await db.delete(role)
    await db.commit()
    return {"message": "Role deleted"}


@router.get("/permissions/list", tags=["RBAC Management"])
async def list_available_permissions(request: Request):
    """List all available permission constants."""
    user_ctx = get_current_user_from_request(request)
    # Viewing permissions requires either creating roles or listing roles context
    # Let's say anyone who can list roles can see permissions to understand them
    authz_service.require_permission(user_ctx, PermissionEnum.ROLE_LIST)

    return [p.value for p in PermissionEnum]
