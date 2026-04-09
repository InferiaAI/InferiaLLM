import logging
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from typing import Optional, List
from fastapi.responses import JSONResponse
from cachetools import TTLCache

from inferia.services.api_gateway.models import UserContext, PermissionEnum
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.database import AsyncSessionLocal
from inferia.services.api_gateway.rbac.permissions import normalize_permissions
from inferia.services.api_gateway.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer()

# Short-TTL cache: (token) → (user, org_id, roles, permissions)
# 30s TTL avoids stale permissions while eliminating 3 DB queries/request
_auth_cache: TTLCache = TTLCache(maxsize=2048, ttl=30)

# Lazy-initialized inferia-auth provider (only created when auth_provider == "inferia-auth")
_inferia_auth_provider = None


def _get_inferia_auth_provider():
    global _inferia_auth_provider
    if _inferia_auth_provider is None:
        from inferia.services.api_gateway.rbac.inferia_auth_provider import InferiaAuthProvider
        _inferia_auth_provider = InferiaAuthProvider(
            base_url=settings.inferia_auth_url,
            public_key_b64=settings.inferia_auth_public_key,
        )
    return _inferia_auth_provider


def _auth_error_response(
    status_code: int, detail: str, headers: Optional[dict] = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers or {},
    )


async def _authenticate_builtin(token: str, request: Request):
    """Authenticate using the built-in JWT + DB auth service."""
    async with AsyncSessionLocal() as db:
        # Validate token and get user (Async)
        user, org_id, roles = await auth_service.get_current_user(db, token)

        # Determine permissions based on roles (Dynamic from DB)
        from sqlalchemy.future import select
        from inferia.services.api_gateway.db.models import Role

        permissions_set = set()
        if roles:
            stmt = select(Role).where(Role.name.in_(roles))
            result = await db.execute(stmt)
            role_records = result.scalars().all()
            for r in role_records:
                if r.permissions:
                    permissions_set.update(r.permissions)

        permissions, _, _ = normalize_permissions(permissions_set)

        quota_limit = 10000
        quota_used = 0

        return UserContext(
            user_id=user.id,
            username=user.email,
            email=user.email,
            roles=roles,
            permissions=permissions,
            org_id=org_id,
            quota_limit=quota_limit,
            quota_used=quota_used,
        )


async def _authenticate_inferia_auth(token: str, request: Request):
    """Authenticate using inferia-auth service, then resolve local roles."""
    provider = _get_inferia_auth_provider()
    claims = await provider.validate_token(token)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Resolve local user and permissions from DB by email
    async with AsyncSessionLocal() as db:
        from sqlalchemy.future import select
        from sqlalchemy import func
        from inferia.services.api_gateway.db.models import (
            User as DBUser,
            UserOrganization,
            Role,
        )

        result = await db.execute(
            select(DBUser).where(func.lower(DBUser.email) == claims.email.lower())
        )
        user = result.scalars().first()

        if not user:
            # Auto-provision: create a local user record for the externally-authenticated user
            user = DBUser(email=claims.email, password_hash="external:inferia-auth")
            db.add(user)
            await db.flush()

            # Link to first available org if any org_ids came from inferia-auth
            from inferia.services.api_gateway.db.models import Organization

            if claims.org_ids:
                org_result = await db.execute(
                    select(Organization).limit(1)
                )
                org = org_result.scalars().first()
                if org:
                    uo = UserOrganization(
                        user_id=user.id, org_id=org.id, role="member"
                    )
                    db.add(uo)
                    user.default_org_id = org.id
            await db.commit()
            await db.refresh(user)

        # Resolve org membership and roles
        org_id = None
        roles = ["member"]

        membership_stmt = (
            select(UserOrganization)
            .where(UserOrganization.user_id == user.id)
            .order_by(UserOrganization.created_at.asc())
        )
        membership_res = await db.execute(membership_stmt)
        membership = membership_res.scalars().first()

        if membership:
            org_id = membership.org_id
            roles = [membership.role]

        # Resolve permissions from roles
        permissions_set = set()
        if roles:
            stmt = select(Role).where(Role.name.in_(roles))
            result = await db.execute(stmt)
            role_records = result.scalars().all()
            for r in role_records:
                if r.permissions:
                    permissions_set.update(r.permissions)

        permissions, _, _ = normalize_permissions(permissions_set)

        return UserContext(
            user_id=user.id,
            username=user.email,
            email=claims.email,
            roles=roles,
            permissions=permissions,
            org_id=org_id,
            quota_limit=10000,
            quota_used=0,
        )


async def auth_middleware(request: Request, call_next):
    """
    Authentication middleware that validates JWT token and extracts user context.
    Adds user context to request.state if authenticated.

    Supports two auth providers controlled by AUTH_PROVIDER env var:
      - "builtin" (default): local JWT validation + DB user lookup
      - "inferia-auth": delegates to inferia-auth service for token validation
    """
    # Skip auth for WebSocket connections - they handle auth differently (via query params)
    # Check both 'upgrade' header and the connection type
    upgrade_header = request.headers.get("upgrade", "").lower()
    connection_header = request.headers.get("connection", "").lower()
    if upgrade_header == "websocket" or "upgrade" in connection_header:
        return await call_next(request)

    # Also skip auth for WebSocket endpoint path
    if request.url.path.startswith("/deployment/ws"):
        return await call_next(request)

    # Skip auth for public endpoints
    public_paths = [
        "/",
        "/health",
        "/health/services",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/auth/login",
        "/auth/register",
        "/auth/refresh",
        "/auth/register-invite",
        "/audit/internal/log",
    ]
    # Allow /auth/invitations/{token} (exactly one segment after prefix)
    invitation_prefix = "/auth/invitations/"
    is_invitation_lookup = (
        request.url.path.startswith(invitation_prefix)
        and "/" not in request.url.path[len(invitation_prefix):]
        and len(request.url.path) > len(invitation_prefix)
    )

    if (
        request.url.path in public_paths
        or request.url.path.startswith("/internal/")
        or is_invitation_lookup
        or request.method == "OPTIONS"
    ):
        return await call_next(request)

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return _auth_error_response(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        scheme, token = auth_header.split()
        if scheme.lower() != "bearer":
            return _auth_error_response(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid authentication scheme",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except ValueError:
        return _auth_error_response(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid Authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check cache first to avoid repeated DB/HTTP queries
    cached = _auth_cache.get(token)
    if cached is not None:
        request.state.user = cached
    else:
        try:
            if settings.auth_provider == "inferia-auth":
                user_context = await _authenticate_inferia_auth(token, request)
            else:
                user_context = await _authenticate_builtin(token, request)

            request.state.user = user_context
            _auth_cache[token] = user_context

        except HTTPException as e:
            return _auth_error_response(
                e.status_code,
                e.detail if isinstance(e.detail, str) else str(e.detail),
                headers=getattr(e, "headers", None),
            )
        except Exception as e:
            logger.exception("Authentication failed")
            return _auth_error_response(
                status.HTTP_401_UNAUTHORIZED,
                f"Authentication failed: {str(e)}",
            )

    response = await call_next(request)
    return response


def get_current_user_from_request(request: Request) -> UserContext:
    """Extract current user from request state."""
    if not hasattr(request.state, "user"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return request.state.user


def require_role(allowed_roles: List[str]):
    """
    Deprecated dependency factory.

    Role-based checks are intentionally disabled in favor of permission-based RBAC.
    """

    def role_dependency(user: UserContext = Depends(get_current_user_from_request)):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Role-based authorization is disabled. "
                "Use permission-based checks via authz_service.require_permission."
            ),
        )

    return role_dependency
