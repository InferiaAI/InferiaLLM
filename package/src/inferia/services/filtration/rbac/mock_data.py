"""
Mock database with sample users, roles, and permissions.
This will be replaced with actual PostgreSQL database in production.
"""

import bcrypt
from datetime import datetime, timedelta, timezone

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import Dict, Optional
from .models import User, Role, RoleType, PermissionType, UserQuota


# ==================== Mock Roles ====================

MOCK_ROLES: Dict[RoleType, Role] = {
    RoleType.ADMIN: Role(
        role_id="role_admin",
        name=RoleType.ADMIN,
        description="Full system access with administrative privileges",
        permissions=[perm for perm in PermissionType],  # All permissions
        quota_limit=10000,  # 10k requests per day
        priority=100
    ),
    RoleType.POWER_USER: Role(
        role_id="role_power",
        name=RoleType.POWER_USER,
        description="Advanced user with access to premium models",
        permissions=[
            PermissionType.ACCESS_GPT4,
            PermissionType.ACCESS_GPT35,
            PermissionType.ACCESS_CLAUDE3,
            PermissionType.ACCESS_LLAMA3,
            PermissionType.ACCESS_MISTRAL,
            PermissionType.STREAMING,
            PermissionType.RAG_ACCESS,
            PermissionType.VIEW_METRICS,
        ],
        quota_limit=5000,  # 5k requests per day
        priority=75
    ),
    RoleType.STANDARD_USER: Role(
        role_id="role_standard",
        name=RoleType.STANDARD_USER,
        description="Standard user with basic model access",
        permissions=[
            PermissionType.ACCESS_GPT35,
            PermissionType.ACCESS_LLAMA3,
            PermissionType.ACCESS_MISTRAL,
            PermissionType.STREAMING,
        ],
        quota_limit=1000,  # 1k requests per day
        priority=50
    ),
    RoleType.GUEST: Role(
        role_id="role_guest",
        name=RoleType.GUEST,
        description="Guest user with limited access",
        permissions=[
            PermissionType.ACCESS_GPT35,
            PermissionType.ACCESS_LLAMA3,
        ],
        quota_limit=100,  # 100 requests per day
        priority=10
    ),
}


# ==================== Mock Users ====================

def hash_pw(password: str) -> str:
    """Helper to hash passwords with bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

MOCK_USERS: Dict[str, User] = {
    "admin": User(
        user_id="user_admin_001",
        username="admin",
        email="admin@inferiallm.com",
        hashed_password=hash_pw("admin123"),
        roles=[RoleType.ADMIN],
        is_active=True,
        created_at=utcnow_naive() - timedelta(days=365),
    ),
    "developer": User(
        user_id="user_dev_001",
        username="developer",
        email="developer@inferiallm.com",
        hashed_password=hash_pw("dev123"),
        roles=[RoleType.POWER_USER],
        is_active=True,
        created_at=utcnow_naive() - timedelta(days=180),
    ),
    "user1": User(
        user_id="user_std_001",
        username="user1",
        email="user1@example.com",
        hashed_password=hash_pw("user123"),
        roles=[RoleType.STANDARD_USER],
        is_active=True,
        created_at=utcnow_naive() - timedelta(days=90),
    ),
    "guest": User(
        user_id="user_guest_001",
        username="guest",
        email="guest@example.com",
        hashed_password=hash_pw("guest123"),
        roles=[RoleType.GUEST],
        is_active=True,
        created_at=utcnow_naive() - timedelta(days=1),
    ),
}


# ==================== Mock User Quotas ====================

MOCK_QUOTAS: Dict[str, UserQuota] = {
    "user_admin_001": UserQuota(
        user_id="user_admin_001",
        quota_limit=10000,
        quota_used=1250,
        reset_at=utcnow_naive() + timedelta(days=1),
    ),
    "user_dev_001": UserQuota(
        user_id="user_dev_001",
        quota_limit=5000,
        quota_used=842,
        reset_at=utcnow_naive() + timedelta(days=1),
    ),
    "user_std_001": UserQuota(
        user_id="user_std_001",
        quota_limit=1000,
        quota_used=234,
        reset_at=utcnow_naive() + timedelta(days=1),
    ),
    "user_guest_001": UserQuota(
        user_id="user_guest_001",
        quota_limit=100,
        quota_used=45,
        reset_at=utcnow_naive() + timedelta(days=1),
    ),
}


# ==================== Database Functions ====================

class MockDatabase:
    """Mock database for storing user and RBAC data."""
    
    def __init__(self):
        self.users = MOCK_USERS.copy()
        self.roles = MOCK_ROLES.copy()
        self.quotas = MOCK_QUOTAS.copy()
    
    def get_user_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        return self.users.get(username)
    
    def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by user_id."""
        for user in self.users.values():
            if user.user_id == user_id:
                return user
        return None
    
    def get_role(self, role_type: RoleType) -> Optional[Role]:
        """Get role by type."""
        return self.roles.get(role_type)
    
    def get_user_permissions(self, user: User) -> list[PermissionType]:
        """Get all permissions for a user based on their roles."""
        permissions = set()
        for role_type in user.roles:
            role = self.get_role(role_type)
            if role:
                permissions.update(role.permissions)
        return list(permissions)
    
    def get_user_quota(self, user_id: str) -> Optional[UserQuota]:
        """Get user quota."""
        return self.quotas.get(user_id)
    
    def increment_quota_usage(self, user_id: str, amount: int = 1) -> bool:
        """Increment user quota usage."""
        quota = self.quotas.get(user_id)
        if quota:
            quota.quota_used += amount
            return True
        return False
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password against hash."""
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


# Global mock database instance
mock_db = MockDatabase()
