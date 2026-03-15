from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from typing import Optional, List
from fastapi.responses import JSONResponse

from inferia.services.api_gateway.models import UserContext, PermissionEnum
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.database import AsyncSessionLocal
from inferia.services.api_gateway.rbac.permissions import normalize_permissions

security = HTTPBearer()

def _auth_error_response(status_code: int, detail: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers or {},
    )


async def auth_middleware(request: Request, call_next):
    """
    Authentication middleware that validates JWT token and extracts user context.
    Adds user context to request.state if authenticated.
    """
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

    # Create DB Session for Auth
    async with AsyncSessionLocal() as db:
        try:
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

            # Mock Quota (until quota model implemented)
            # Default to high limit
            quota_limit = 10000
            quota_used = 0

            # Create user context
            user_context = UserContext(
                user_id=user.id,
                username=user.email,
                email=user.email,
                roles=roles,
                permissions=permissions,
                org_id=org_id,
                quota_limit=quota_limit,
                quota_used=quota_used,
                # is_active=True, # user.is_active if column exists
            )

            # Add user context to request state
            request.state.user = user_context

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
