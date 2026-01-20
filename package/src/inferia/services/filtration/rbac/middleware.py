from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from typing import Optional, List

from models import UserContext, PermissionEnum
from rbac.auth import auth_service
from db.database import AsyncSessionLocal

security = HTTPBearer()



async def auth_middleware(request: Request, call_next):
    """
    Authentication middleware that validates JWT token and extracts user context.
    Adds user context to request.state if authenticated.
    """
    # Skip auth for public endpoints
    public_paths = [
        "/", "/health", "/docs", "/redoc", "/openapi.json", 
        "/auth/login", "/auth/register", "/auth/refresh", "/auth/register-invite",
        "/audit/internal/log"
    ]
    if (request.url.path in public_paths or 
        request.url.path.startswith("/internal") or 
        request.url.path.startswith("/auth/invitations/") or
        request.method == "OPTIONS"):
        return await call_next(request)
    
    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        scheme, token = auth_header.split()
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create DB Session for Auth
    async with AsyncSessionLocal() as db:
        try:
            # Validate token and get user (Async)
            user, org_id, roles = await auth_service.get_current_user(db, token)
            
            # Determine permissions based on roles (Dynamic from DB)
            from sqlalchemy.future import select
            from db.models import Role
            
            permissions_set = set()
            if roles:
                stmt = select(Role).where(Role.name.in_(roles))
                result = await db.execute(stmt)
                role_records = result.scalars().all()
                for r in role_records:
                    if r.permissions:
                        permissions_set.update(r.permissions)
            
            permissions = list(permissions_set)
            
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
            # Re-raise HTTP exceptions
            raise e
        except Exception as e:
             # Log error?
             raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Authentication failed: {str(e)}",
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
    Dependency factory to enforce role-based access control.
    """
    def role_dependency(user: UserContext = Depends(get_current_user_from_request)):
        # Normalize roles to set for O(1) lookup
        user_roles = set(user.roles or [])
        
        # Check if user has at least one of the allowed roles
        # Note: We assume role names in DB match those passed here (e.g. "admin", "developer")
        
        # If admin is in allowed_roles, checking intersection handles it.
        # But if we want Admin to ALWAYS have access regardless of allowed_roles (unless implicit),
        # typically we explicitly check. For now, let's stick to the requested list.
        
        has_permission = False
        for role in allowed_roles:
            if role in user_roles:
                has_permission = True
                break
        
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted. Required roles: {allowed_roles}",
            )
        return user
    return role_dependency
