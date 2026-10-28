from typing import List
from fastapi import HTTPException, status

from api_gateway.models import PermissionEnum, UserContext

# Role to Permission Mapping
# Role to Permission Mapping - DEPRECATED
# Permissions are now managed dynamically via Database & Role Management API
ROLE_PERMISSIONS = {}

class AuthorizationService:
    """Authorization service for permission and policy checking."""
    
    def __init__(self):
        pass
    
    def get_user_permissions(self, user: UserContext) -> List[str]:
        """Get all permissions for a user."""
        # Use existing permissions in context if available, or fetch from role mapping
        if hasattr(user, "permissions") and user.permissions:
             return user.permissions
             
        # Fallback to role-based mapping
        permissions = []
        for role in user.roles:
            role_perms = ROLE_PERMISSIONS.get(role, [])
            # Convert enums to strings if needed
            permissions.extend([p.value if hasattr(p, "value") else p for p in role_perms])
            
        return list(set(permissions))
    
    def has_permission(self, user: UserContext, permission: PermissionEnum) -> bool:
        """Check if user has a specific permission."""
        user_permissions = self.get_user_permissions(user)
        return permission.value in user_permissions
    
    def require_permission(self, user: UserContext, permission: PermissionEnum) -> None:
        """Require user to have a specific permission, raise exception if not."""
        if not self.has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission.value} required",
            )
            
    def is_superadmin(self, user: UserContext) -> bool:
        """The install's superadmin, signed in with its local password.

        SSO users map to local users by email, so the email alone would let an
        IdP account carrying the superadmin's address through.
        """
        from api_gateway.config import settings

        email = (settings.superadmin_email or "").strip().lower()
        return bool(email) and user.local_session and (
            (user.email or "").strip().lower() == email
        )

    def require_superadmin(self, user: UserContext) -> None:
        """For install-wide state every org shares: roles, provider credentials."""
        if not self.is_superadmin(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the superadmin can change this; it applies to every organization",
            )

    def require_grantable(self, user: UserContext, role_permissions) -> None:
        """403 unless *user* already holds every permission the role grants."""
        if self.is_superadmin(user):
            return
        from api_gateway.rbac.permissions import normalize_permissions

        granted, _, _ = normalize_permissions(role_permissions or [])
        missing = set(granted) - set(self.get_user_permissions(user))
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot grant a role with permissions you do not have",
            )

    async def require_grantable_role(self, db, user: UserContext, role_name: str) -> None:
        """require_grantable for a role by name; 400 if no such role exists."""
        from sqlalchemy.future import select
        from api_gateway.db.models import Role

        role = (await db.execute(select(Role).where(Role.name == role_name))).scalars().first()
        if role is None:
            raise HTTPException(status_code=400, detail=f"Role '{role_name}' does not exist")
        self.require_grantable(user, role.permissions)

    def require_role(self, user: UserContext, role: str) -> None:
        """Deprecated: Role-based checks are disabled."""
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Role-based authorization is disabled. "
                "Use require_permission with PermissionEnum."
            ),
        )
    
    def can_access_model(self, user: UserContext, model_id: str) -> bool:
        """Check if user can access a specific model."""
        # For now, we only check general model access permission.
        # Granular per-model permissions can be re-implemented if needed.
        return self.has_permission(user, PermissionEnum.MODEL_ACCESS)
    
    def require_model_access(self, user: UserContext, model_id: str) -> None:
        """Require user to have access to a specific model."""
        if not self.can_access_model(user, model_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: You don't have permission to use model '{model_id}'",
            )
    
    def get_allowed_models(self, user: UserContext) -> List[str]:
        """Get list of models the user can access."""
        # Simplified: If user has MODEL_ACCESS, they can access all available models.
        # Future: Implement fine-grained checks if needed.
        if self.has_permission(user, PermissionEnum.MODEL_ACCESS):
            # Returning a wildcard or list of all models would be ideal, 
            # but for now we follow the pattern of returning specific strings if they were utilized.
            # Since the previous code was broken/unused, we can safe-return a broad list 
            # or rely on the fact that 'model:access' is the gatekeeper.
            return ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "claude-3-opus", "claude-3-sonnet", "llama-3-70b", "llama-3-8b", "mistral-7b", "mistral-medium"]
        
        return []


# Global authorization service instance
authz_service = AuthorizationService()
