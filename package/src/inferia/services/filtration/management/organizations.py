from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import uuid
import secrets

from db.database import get_db
from db.models import Organization as DBOrganization, Invitation as DBInvitation, User as DBUser
from schemas.management import (
    OrganizationCreate, OrganizationResponse,
    InviteRequest, InviteResponse, InvitationListResponse
)
from management.dependencies import get_current_user_context
from datetime import datetime, timedelta
from schemas.auth import PermissionEnum
from rbac.authorization import authz_service

router = APIRouter(tags=["Organizations"])

@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    org_data: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    request: Request = None
):
    # Generate API Key
    api_key = f"sk-inferia-{uuid.uuid4()}"
    
    new_org = DBOrganization(
        name=org_data.name,
        api_key=api_key
    )
    db.add(new_org)
    await db.commit()
    await db.refresh(new_org)
    return new_org

@router.get("/organizations/me", response_model=OrganizationResponse)
async def get_my_organization(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    
    if not user_ctx.org_id:
         raise HTTPException(status_code=400, detail="No active organization context found in token")
        
    org_result = await db.execute(select(DBOrganization).where(DBOrganization.id == user_ctx.org_id))
    org = org_result.scalars().first()
    
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
        
    return org

# --- Invitations (Grouped with Org Management) ---

@router.post("/invitations", response_model=InviteResponse, status_code=201)
async def create_invitation(
    invite_data: InviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MEMBER_INVITE)
    
    if not user_ctx.org_id:
        raise HTTPException(status_code=400, detail="Requester must belong to an organization")
        
    # Check if user is already a member of *this* organization
    existing_user_result = await db.execute(select(DBUser).where(DBUser.email == invite_data.email))
    existing_user = existing_user_result.scalars().first()
    
    if existing_user:
        # Check membership
        from db.models import UserOrganization
        membership = await db.execute(select(UserOrganization).where(
            UserOrganization.user_id == existing_user.id,
            UserOrganization.org_id == user_ctx.org_id
        ))
        if membership.scalars().first():
             raise HTTPException(status_code=400, detail="User is already a member of this organization")

    # Check for existing pending invitation
    existing_invite = await db.execute(select(DBInvitation).where(
        DBInvitation.email == invite_data.email,
        DBInvitation.org_id == user_ctx.org_id,
        DBInvitation.accepted_at == None,
        DBInvitation.expires_at > datetime.utcnow()
    ))
    if existing_invite.scalars().first():
         raise HTTPException(status_code=400, detail="Pending invitation already exists for this email")

    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=48)
    
    new_invite = DBInvitation(
        email=invite_data.email,
        role=invite_data.role,
        org_id=user_ctx.org_id,
        created_by=user_ctx.user_id,
        token=token,
        expires_at=expires
    )
    
    db.add(new_invite)
    await db.commit()
    await db.refresh(new_invite)
    
    base_url = "http://localhost:5173/dashboard"
    invite_link = f"{base_url}/auth/accept-invite?token={token}"
    
    return InviteResponse(
        id=new_invite.id,
        email=new_invite.email,
        role=new_invite.role,
        token=new_invite.token,
        invite_link=invite_link,
        status="pending",
        expires_at=new_invite.expires_at,
        created_at=new_invite.created_at
    )

@router.get("/invitations", response_model=InvitationListResponse)
async def list_invitations(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MEMBER_LIST)
    
    if not user_ctx.org_id:
        return InvitationListResponse(invitations=[])

    invites_query = select(DBInvitation).where(
        DBInvitation.org_id == user_ctx.org_id,
        DBInvitation.accepted_at == None,
        DBInvitation.expires_at > datetime.utcnow()
    )
    invites = await db.execute(invites_query)
    
    response_list = []
    base_url = "http://localhost:5173"
    
    for inv in invites.scalars().all():
        response_list.append(InviteResponse(
            id=inv.id,
            email=inv.email,
            role=inv.role,
            token=inv.token,
            invite_link=f"{base_url}/auth/accept-invite?token={inv.token}",
            status="pending",
            expires_at=inv.expires_at,
            created_at=inv.created_at
        ))
        
    return InvitationListResponse(invitations=response_list)

@router.delete("/invitations/{invite_id}", status_code=204)
async def revoke_invitation(
    invite_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.MEMBER_DELETE)

    invite = await db.get(DBInvitation, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found")
        
    if invite.org_id != user_ctx.org_id:
        raise HTTPException(status_code=403, detail="Invitation belongs to different organization")
        
    await db.delete(invite)
    await db.commit()
