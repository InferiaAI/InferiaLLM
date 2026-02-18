from typing import List, Optional
from fastapi import HTTPException, status

from inferia.services.api_gateway.models import PermissionEnum, UserContext

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
            
    def require_role(self, user: UserContext, role: str) -> None:
        """Require user to have a specific role."""
        if role not in user.roles:
             raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Role '{role}' required",
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
