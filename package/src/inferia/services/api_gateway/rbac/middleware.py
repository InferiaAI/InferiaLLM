from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from typing import Optional, List
from fastapi.responses import JSONResponse
from cachetools import TTLCache
import logging

from inferia.services.api_gateway.models import UserContext, PermissionEnum
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.db.database import AsyncSessionLocal
from inferia.services.api_gateway.rbac.permissions import normalize_permissions
from inferia.services.api_gateway.rbac.jwks_verifier import (
    JWKSVerifier,
    JWKSVerifyError,
)
from inferia.services.api_gateway.rbac.shadow_user import get_or_create_shadow_user

logger = logging.getLogger(__name__)

security = HTTPBearer()

# Short-TTL cache: (token) → (user, org_id, roles, permissions)
# 30s TTL avoids stale permissions while eliminating 3 DB queries/request
_auth_cache: TTLCache = TTLCache(maxsize=2048, ttl=30)


def _auth_error_response(
    status_code: int, detail: str, headers: Optional[dict] = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers or {},
    )


async def _resolve_local_token(db, token: str) -> UserContext:
    """Validate a locally-issued JWT and build UserContext."""
    user, org_id, roles = await auth_service.get_current_user(db, token)

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

    return UserContext(
        user_id=user.id,
        username=user.email,
        email=user.email,
        roles=roles,
        permissions=permissions,
        org_id=org_id,
        quota_limit=10000,
        quota_used=0,
    )


# Lazily-built JWKSVerifier singleton. Re-instantiating per request would
# defeat the JWKS cache and force a network round-trip on every API call.
_verifier: Optional[JWKSVerifier] = None


def _get_verifier() -> JWKSVerifier:
    global _verifier
    if _verifier is None:
        _verifier = JWKSVerifier(
            jwks_url=settings.external_auth_url.rstrip("/") + "/.well-known/jwks.json",
            issuer=settings.external_auth_issuer,
            audience=settings.app_namespace,
            cache_ttl=settings.oauth_jwks_cache_ttl_seconds,
        )
    return _verifier


async def _resolve_external_token(db, token: str) -> UserContext:
    """Verify an inferia-auth-issued JWT and build a UserContext.

    Roles and permissions come straight from the JWT claims — the local
    DB is consulted only to mint/resolve a shadow-user row so that org
    memberships, audit log foreign keys, and API key references remain
    valid. This is per spec §9.1: 'roles and permissions come straight
    from the JWT'.
    """
    try:
        claims = _get_verifier().verify_sync(token)
    except JWKSVerifyError as e:
        logger.info("External token verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = claims.get("sub", "")
    external_user_id = sub.split(":", 1)[1] if ":" in sub else sub
    email = claims.get("email", "")

    user, _local_org_id, _local_roles = await get_or_create_shadow_user(
        db, email=email, external_id=external_user_id
    )

    # org_id source-of-truth: explicit 'org_id' claim if present, else
    # first entry of org_ids[], else None.
    org_id = claims.get("org_id")
    if not org_id:
        org_ids = claims.get("org_ids") or []
        org_id = org_ids[0] if org_ids else None

    return UserContext(
        user_id=user.id,
        username=user.email,
        email=user.email,
        roles=list(claims.get("roles") or []),
        permissions=list(claims.get("permissions") or []),
        org_id=org_id,
        quota_limit=10000,
        quota_used=0,
    )


async def auth_middleware(request: Request, call_next):
    """
    Authentication middleware that validates JWT token and extracts user context.
    Adds user context to request.state if authenticated.

    When AUTH_PROVIDER=external, tokens are validated in two steps:
      1. Try local JWT decode (covers superadmin and locally-issued tokens).
      2. If local decode fails, call inferia-auth introspect.
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

    # Skip user-auth for the inferia-worker control-plane endpoints. Workers
    # authenticate with their own bootstrap-JWT (POST /v1/workers/register)
    # and worker-JWT (WS /v1/workers/channel), neither of which match the
    # user-token shape this middleware enforces.
    if request.url.path.startswith("/v1/workers/"):
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
        "/auth/start",
        "/auth/callback",
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

    # Check cache first to avoid DB/network queries per request
    cached = _auth_cache.get(token)
    if cached is not None:
        request.state.user = cached
    else:
        use_external = settings.auth_provider == "external" and settings.external_auth_url

        async with AsyncSessionLocal() as db:
            try:
                if not use_external:
                    # Pure local auth
                    user_context = await _resolve_local_token(db, token)
                else:
                    # External mode: try local first (superadmin), fall back to external
                    try:
                        user_context = await _resolve_local_token(db, token)
                    except HTTPException:
                        user_context = await _resolve_external_token(db, token)

                request.state.user = user_context
                _auth_cache[token] = user_context

            except HTTPException as e:
                return _auth_error_response(
                    e.status_code,
                    e.detail if isinstance(e.detail, str) else str(e.detail),
                    headers=getattr(e, "headers", None),
                )
            except Exception as e:
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
