from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from inferia.services.api_gateway.models import (
    AuthToken,
    LoginRequest,
    UserInfoResponse,
    PermissionEnum,
    RegisterRequest,
    RegisterInviteRequest,
    InviteResponse,
    TOTPSetupResponse,
    TOTPVerifyRequest,
)
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.rbac.middleware import get_current_user_from_request

# from rbac.authorization import authz_service # Temporarily disabled or needs refactor
from inferia.services.api_gateway.db.database import get_db
from inferia.services.api_gateway.db.models import (
    User as DBUser,
    Organization as DBOrganization,
    Invitation as DBInvitation,
    UserOrganization,
)
from inferia.services.api_gateway.models import OrganizationBasicInfo, SwitchOrgRequest
from sqlalchemy.future import select
import uuid
import secrets
import os
from datetime import datetime, timezone
import pyotp
import qrcode
import io
import base64

# Fix import path for common module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from inferia.common.rate_limit import login_rate_limiter, register_rate_limiter


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


router = APIRouter(prefix="/auth", tags=["Authentication"])

security = HTTPBearer()


@router.post("/login", response_model=AuthToken)
async def login(
    request: LoginRequest, http_request: Request, db: AsyncSession = Depends(get_db)
):
    """
    Login endpoint to authenticate user and receive JWT tokens.
    Authentication against Postgres Database.
    Supports TOTP 2FA.
    Rate limited: 5 attempts per minute.
    """
    # Rate limiting check
    client_ip = http_request.client.host if http_request.client else "unknown"
    forwarded = http_request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    is_allowed, retry_after = login_rate_limiter.is_allowed(request.username, client_ip)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    # 1. Authenticate user credentials first
    user = await auth_service.authenticate_user(db, request.username, request.password)

    if not user:
        # Log failed logic
        await auth_service.log_failed_login(db, request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Check 2FA
    enable_2fa = os.getenv("ENABLE_2FA", "true").lower() == "true"

    if user.totp_enabled and enable_2fa:
        if not request.totp_code:
            # Return specific error code for frontend to prompt 2FA
            raise HTTPException(
                status_code=403,
                detail="TOTP_REQUIRED",
            )

        # Verify Code
        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(request.totp_code):
            raise HTTPException(
                status_code=401,
                detail="Invalid 2FA Code",
            )

    # 3. Proceed to Login
    return await auth_service.login(db, request)


@router.post("/register", response_model=AuthToken)
async def register(reg_data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Public registration is DISABLED.
    Use invitation links to register.
    """
    raise HTTPException(
        status_code=403,
        detail="Public registration is disabled. Please ask an administrator for an invitation.",
    )


@router.post("/register-invite", response_model=AuthToken)
async def register_invite(
    reg_data: RegisterInviteRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new user via invitation.
    Rate limited: 3 attempts per hour per IP.
    """
    # Rate limiting check
    client_ip = http_request.client.host if http_request.client else "unknown"
    forwarded = http_request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    is_allowed, retry_after = register_rate_limiter.is_allowed("invite", client_ip)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many registration attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    # 1. Validate Invite
    invite_query = select(DBInvitation).where(DBInvitation.token == reg_data.token)
    invite_result = await db.execute(invite_query)
    invite = invite_result.scalars().first()

    if not invite:
        raise HTTPException(status_code=404, detail="Invalid invitation token")
    if invite.accepted_at:
        raise HTTPException(status_code=400, detail="Invitation already accepted")
    if invite.expires_at < utcnow_naive():
        raise HTTPException(status_code=400, detail="Invitation expired")

    # 2. Check if user exists (Should use Accept Invite flow if exists, but handling edge cases)
    existing = await db.execute(select(DBUser).where(DBUser.email == invite.email))
    if existing.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="User already exists. Please login and accept the invitation.",
        )

    # 3. Create User
    hashed_pw = auth_service.get_password_hash(reg_data.password)
    new_user = DBUser(
        email=invite.email,
        password_hash=hashed_pw,
        default_org_id=invite.org_id,  # Set default org to invite org
    )
    db.add(new_user)
    await db.flush()  # Get ID

    # 4. Create UserOrganization link
    uo = UserOrganization(user_id=new_user.id, org_id=invite.org_id, role=invite.role)
    db.add(uo)

    # 5. Mark invite accepted
    invite.accepted_at = utcnow_naive()

    await db.commit()
    await db.refresh(new_user)

    # 6. Organization Info
    stmt_org = select(DBOrganization).where(DBOrganization.id == invite.org_id)
    org_res = await db.execute(stmt_org)
    target_org = org_res.scalars().first()

    org_info = OrganizationBasicInfo(
        id=target_org.id, name=target_org.name, role=invite.role
    )

    # 7. Auto Login
    access_token = auth_service.create_access_token(
        new_user, org_id=invite.org_id, role=invite.role
    )
    refresh_token = auth_service.create_refresh_token(new_user, org_id=invite.org_id)

    return AuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=auth_service.access_token_expire_minutes * 60,
        organizations=[org_info],
    )


@router.post("/accept-invite", response_model=AuthToken)
async def accept_invitation(
    request: Request,
    token: str,  # passed as query param or body? Let's assume query for simplicity or body wrapper
    db: AsyncSession = Depends(get_db),
):
    """
    Accept an invitation to join an organization.
    User must be authenticated.
    """
    user_context = get_current_user_from_request(request)

    # Validate Invite
    invite_query = select(DBInvitation).where(DBInvitation.token == token)
    invite_result = await db.execute(invite_query)
    invite = invite_result.scalars().first()

    if not invite:
        raise HTTPException(status_code=404, detail="Invalid invitation token")
    if invite.accepted_at:
        raise HTTPException(status_code=400, detail="Invitation already accepted")
    if invite.expires_at < utcnow_naive():
        raise HTTPException(status_code=400, detail="Invitation expired")

    # Check if email matches current user?
    # Logic: User A (email A) might be invited as email B?
    # Strict matching is safer.
    if invite.email.lower() != user_context.email.lower():
        raise HTTPException(
            status_code=403, detail="Invitation email does not match logged in user"
        )

    # Check if already member
    stmt_check = select(UserOrganization).where(
        UserOrganization.user_id == user_context.user_id,
        UserOrganization.org_id == invite.org_id,
    )
    existing_membership = await db.execute(stmt_check)
    if existing_membership.scalars().first():
        # Already member, just mark accepted if not?
        pass
    else:
        # Link User
        uo = UserOrganization(
            user_id=user_context.user_id, org_id=invite.org_id, role=invite.role
        )
        db.add(uo)

    # Mark invite accepted
    invite.accepted_at = utcnow_naive()
    # db.add(invite) # Already attached

    await db.commit()

    # Return updated token with new org context?
    # Or just return success. But frontend might want to switch immediately.
    # Let's return new AuthToken context switched to new org.

    user_res = await db.execute(select(DBUser).where(DBUser.id == user_context.user_id))
    user = user_res.scalars().first()

    # Fetch all orgs for token
    stmt_orgs = (
        select(UserOrganization, DBOrganization)
        .join(DBOrganization, UserOrganization.org_id == DBOrganization.id)
        .where(UserOrganization.user_id == user.id)
    )
    orgs_res = await db.execute(stmt_orgs)
    orgs_data = orgs_res.all()
    org_list = [
        OrganizationBasicInfo(id=org.id, name=org.name, role=link.role)
        for link, org in orgs_data
    ]

    # Generate tokens for the NEW org
    access_token = auth_service.create_access_token(
        user, org_id=invite.org_id, role=invite.role
    )
    refresh_token = auth_service.create_refresh_token(user, org_id=invite.org_id)

    return AuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=auth_service.access_token_expire_minutes * 60,
        organizations=org_list,
    )


@router.get("/invitations/{token}", response_model=InviteResponse)
async def get_invite_info(token: str, db: AsyncSession = Depends(get_db)):
    """Get public invitation info by token (for UI display)."""
    invite_query = select(DBInvitation).where(DBInvitation.token == token)
    result = await db.execute(invite_query)
    invite = result.scalars().first()

    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if invite.expires_at < utcnow_naive():
        raise HTTPException(status_code=400, detail="Invitation expired")

    if invite.accepted_at:
        raise HTTPException(status_code=400, detail="Invitation already accepted")

    # We mock invite link here as we don't store it, just reconstruct for response/validation
    base_url = "http://localhost:3001"
    return InviteResponse(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        token=invite.token,
        invite_link=f"{base_url}/auth/accept-invite?token={invite.token}",
        status="pending",
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


@router.post("/refresh", response_model=AuthToken)
async def refresh_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Refresh access token using refresh token."""
    # Note: refresh logic also needs DB update if strictly following pattern,
    # but verify_token doesn't check DB in current implementation (stateless).
    return auth_service.refresh_access_token(credentials.credentials)


@router.post("/logout")
async def logout():
    """
    Logout endpoint (placeholder for future session management).
    In a stateless JWT setup, logout is typically handled client-side by discarding tokens.
    """
    return {"message": "Successfully logged out"}


@router.get("/organizations")
async def list_organizations(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of organizations to skip"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of organizations to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """List organizations the current user belongs to with pagination."""
    user_context = get_current_user_from_request(request)

    stmt = (
        select(UserOrganization, DBOrganization)
        .join(DBOrganization, UserOrganization.org_id == DBOrganization.id)
        .where(UserOrganization.user_id == user_context.user_id)
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(stmt)
    orgs_data = result.all()

    org_list = []
    for uo, org in orgs_data:
        org_list.append(OrganizationBasicInfo(id=org.id, name=org.name, role=uo.role))

    return org_list


@router.post("/switch-org", response_model=AuthToken)
async def switch_organization(
    data: SwitchOrgRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """Switch user context to another organization."""
    user_context = get_current_user_from_request(request)

    # Verify membership
    stmt = select(UserOrganization).where(
        UserOrganization.user_id == user_context.user_id,
        UserOrganization.org_id == data.org_id,
    )
    result = await db.execute(stmt)
    uo = result.scalars().first()

    if not uo:
        raise HTTPException(
            status_code=403, detail="User is not a member of this organization"
        )

    # Get User object
    user_res = await db.execute(select(DBUser).where(DBUser.id == user_context.user_id))
    user = user_res.scalars().first()

    # Update default Org? Optionally. Let's do it for persistence.
    user.default_org_id = data.org_id
    await db.commit()

    # Return new tokens
    access_token = auth_service.create_access_token(
        user, org_id=data.org_id, role=uo.role
    )
    refresh_token = auth_service.create_refresh_token(user, org_id=data.org_id)

    # We should return orgs list too? AuthToken model has it.
    # We can fetch them or just not return (empty list implies only token update).
    # Ideally frontend might want fresh org list.

    # Reuse list logic
    stmt_orgs = (
        select(UserOrganization, DBOrganization)
        .join(DBOrganization, UserOrganization.org_id == DBOrganization.id)
        .where(UserOrganization.user_id == user.id)
    )
    orgs_res = await db.execute(stmt_orgs)
    orgs_data = orgs_res.all()
    org_list = [
        OrganizationBasicInfo(id=org.id, name=org.name, role=uo.role)
        for uo, org in orgs_data
    ]

    return AuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=auth_service.access_token_expire_minutes * 60,
        organizations=org_list,
    )


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current authenticated user information."""
    user_context = get_current_user_from_request(request)

    # Fetch fresh user data for status flags
    stmt = select(DBUser).where(DBUser.id == user_context.user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Map UserContext pydantic model to UserInfoResponse
    return UserInfoResponse(
        user_id=user_context.user_id,
        username=user_context.username,
        email=user_context.email,
        roles=user_context.roles,
        created_at=user.created_at,
        is_active=user_context.is_active,
        totp_enabled=user.totp_enabled,
    )


@router.get("/permissions")
async def get_user_permissions(request: Request):
    """Get current user's permissions and allowed models."""
    user_context = get_current_user_from_request(request)
    permissions = user_context.permissions

    allowed_models = ["gpt-4", "gpt-3.5-turbo"]  # Temporary fallback


@router.post("/totp/setup", response_model=TOTPSetupResponse)
async def totp_setup(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Generate secret and QR code for TOTP setup.
    User must be authenticated.
    """
    user_context = get_current_user_from_request(request)

    # Generate Secret
    secret = pyotp.random_base32()

    # Generate QR Code
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user_context.email, issuer_name="Inferia LLM"
    )

    img = qrcode.make(uri)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    qr_b64 = base64.b64encode(buffered.getvalue()).decode()

    # Store secret temporarily or in DB?
    # Fetch User
    stmt = select(DBUser).where(DBUser.id == user_context.user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.totp_secret = secret
    # user.totp_enabled = False # Keep false until verified
    await db.commit()

    return TOTPSetupResponse(secret=secret, qr_code=f"data:image/png;base64,{qr_b64}")


@router.post("/totp/verify")
async def totp_verify(
    payload: TOTPVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """
    Verify and enable TOTP.
    """
    user_context = get_current_user_from_request(request)

    stmt = select(DBUser).where(DBUser.id == user_context.user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or not user.totp_secret:
        raise HTTPException(status_code=400, detail="TOTP not setup requested")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(payload.totp_code):
        raise HTTPException(status_code=400, detail="Invalid Code")

    user.totp_enabled = True
    await db.commit()

    return {"status": "enabled"}


@router.post("/totp/disable")
async def totp_disable(request: Request, db: AsyncSession = Depends(get_db)):
    """Disable TOTP."""
    user_context = get_current_user_from_request(request)

    stmt = select(DBUser).where(DBUser.id == user_context.user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()

    return {"status": "disabled"}
