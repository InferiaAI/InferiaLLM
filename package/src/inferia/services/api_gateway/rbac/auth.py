from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from fastapi import HTTPException, status
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
import bcrypt

from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.models import AuthToken, TokenPayload, LoginRequest, OrganizationBasicInfo
from inferia.services.api_gateway.db.models import User as DBUser, UserOrganization, Organization
from inferia.services.api_gateway.audit.service import audit_service
from inferia.services.api_gateway.models import AuditLogCreate

def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class AuthService:
    """Authentication service for handling JWT tokens."""
    
    def __init__(self):
        self.secret_key = settings.jwt_secret_key
        self.algorithm = settings.jwt_algorithm
        self.access_token_expire_minutes = settings.access_token_expire_minutes
        self.refresh_token_expire_days = settings.refresh_token_expire_days
    
    def verify_password(self, plain_password, hashed_password):
        # hashed_password from DB is string, needs to be bytes
        if isinstance(hashed_password, str):
            hashed_password = hashed_password.encode('utf-8')
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password)

    def get_password_hash(self, password):
        # Returns bytes, decode to string for DB storage
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        return hashed.decode('utf-8')

    def create_access_token(self, user: DBUser, org_id: str, role: str) -> str:
        """Create JWT access token with org context."""
        expire = utcnow_naive() + timedelta(minutes=self.access_token_expire_minutes)
        
        # Role is now passed explicitly
        roles = [role]

        payload = {
            "sub": user.id, # Using UUID
            "username": user.email, # Using email as username
            "email": user.email,
            "exp": expire,
            "iat": utcnow_naive(),
            "type": "access",
            "roles": roles,
            "org_id": org_id
        }
        
        encoded_jwt = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def create_refresh_token(self, user: DBUser, org_id: Optional[str] = None) -> str:
        """Create JWT refresh token."""
        expire = utcnow_naive() + timedelta(days=self.refresh_token_expire_days)
        
        payload = {
            "sub": user.id,
            "exp": expire,
            "iat": utcnow_naive(),
            "type": "refresh",
            "org_id": org_id or user.default_org_id
        }
        
        encoded_jwt = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def decode_token(self, token: str) -> TokenPayload:
        """Decode and validate JWT token."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            token_payload = TokenPayload(
                sub=payload.get("sub"),
                exp=payload.get("exp"),
                iat=payload.get("iat"),
                type=payload.get("type"),
                roles=payload.get("roles", []),
                org_id=payload.get("org_id"),
            )
            return token_payload
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    async def authenticate_user(self, db: AsyncSession, username: str, password: str) -> Optional[DBUser]:
        """Authenticate user with username (email) and password."""
        # Query DB for user by email
        result = await db.execute(select(DBUser).where(DBUser.email == username))
        user = result.scalars().first()
        
        if not user:
            return None
        if not self.verify_password(password, user.password_hash):
            return None
        return user
    
    async def log_failed_login(self, db: AsyncSession, username: str):
        """Log failed login attempt."""
        await audit_service.log_event(
            db,
            AuditLogCreate(
                action="user.login",
                details={"username": username, "reason": "authentication_failed"},
                status="failure"
            )
        )

    async def login(self, db: AsyncSession, request: LoginRequest) -> AuthToken:
        """Login user and return JWT tokens."""
        user = await self.authenticate_user(db, request.username, request.password)
        
        if not user:
            # Log failed login attempt
            await self.log_failed_login(db, request.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Fetch user organizations
        stmt = select(UserOrganization, Organization).join(
            Organization, UserOrganization.org_id == Organization.id
        ).where(UserOrganization.user_id == user.id)
        
        result = await db.execute(stmt)
        orgs_data = result.all() # [(UserOrganization, Organization), ...]
        
        if not orgs_data:
             # AUTO-FIX: If single tenant, add user to default org if they have none
             stmt = select(Organization).limit(1)
             result = await db.execute(stmt)
             default_org = result.scalars().first()
             
             if default_org:
                 # Add user to org
                 uo = UserOrganization(user_id=user.id, org_id=default_org.id, role="member")
                 db.add(uo)
                 await db.commit()
                 
                 # Refetch
                 stmt = select(UserOrganization, Organization).join(
                    Organization, UserOrganization.org_id == Organization.id
                 ).where(UserOrganization.user_id == user.id)
                 result = await db.execute(stmt)
                 orgs_data = result.all()
             
             if not orgs_data:
                 raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User does not belong to any organization",
                )
            
        # Select target org (default or first)
        target_org_id = user.default_org_id
        active_role = "member"
        active_org = None
        
        org_list = []
        found_target = False
        
        for uo, org in orgs_data:
            org_list.append(OrganizationBasicInfo(id=org.id, name=org.name, role=uo.role))
            if target_org_id and uo.org_id == target_org_id:
                active_role = uo.role
                active_org = org
                found_target = True
            elif not target_org_id and not active_org: # Pick first if no default
                active_role = uo.role
                active_org = org
                target_org_id = org.id
                found_target = True

        if not found_target:
             # Fallback to first if default invaid
             uo, org = orgs_data[0]
             active_role = uo.role
             target_org_id = org.id
        
        access_token = self.create_access_token(user, org_id=target_org_id, role=active_role)
        refresh_token = self.create_refresh_token(user, org_id=target_org_id)
        
        # Log successful login
        await audit_service.log_event(
            db,
            AuditLogCreate(
                user_id=user.id,
                action="user.login",
                details={
                    "org_id": target_org_id,
                    "role": active_role
                },
                status="success"
            )
        )

        return AuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=self.access_token_expire_minutes * 60,
            organizations=org_list
        )
    
    async def get_current_user(self, db: AsyncSession, token: str) -> tuple[DBUser, Optional[str], list[str]]:
        """Get current user from token. Returns (user, org_id, roles)."""
        token_payload = self.decode_token(token)
        
        if token_payload.type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
        
        result = await db.execute(select(DBUser).where(DBUser.id == token_payload.sub))
        user = result.scalars().first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        
        return user, token_payload.org_id, token_payload.roles

    async def refresh_access_token(self, refresh_token: str, db: AsyncSession) -> AuthToken:
        """Refresh access token using refresh token."""
        try:
            payload = self.decode_token(refresh_token)
            if payload.type != "refresh":
                 raise HTTPException(status_code=401, detail="Invalid token type")
            
            user_id = payload.sub
            org_id = getattr(payload, "org_id", None)
            
            result = await db.execute(select(DBUser).where(DBUser.id == user_id))
            user = result.scalars().first()
            if not user:
                raise HTTPException(status_code=401, detail="User not found")
            
            # If org_id in token, verify user still has access
            target_org_id = org_id or user.default_org_id
            target_role = "member"
            
            if target_org_id:
                stmt = select(UserOrganization).where(
                    UserOrganization.user_id == user.id,
                    UserOrganization.org_id == target_org_id
                )
                res = await db.execute(stmt)
                uo = res.scalars().first()
                if uo:
                    target_role = uo.role
                else:
                     target_org_id = user.default_org_id
                     if target_org_id:
                         stmt = select(UserOrganization).where(
                             UserOrganization.user_id == user.id,
                             UserOrganization.org_id == target_org_id
                         )
                         res = await db.execute(stmt)
                         uo = res.scalars().first()
                         if uo:
                            target_role = uo.role
            
            access_token = self.create_access_token(user, org_id=target_org_id, role=target_role)
            
            return AuthToken(
                access_token=access_token,
                refresh_token=refresh_token, # Reuse same refresh token
                token_type="bearer",
                expires_in=self.access_token_expire_minutes * 60
            )

        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Refresh failed: {str(e)}")


# Global auth service instance
auth_service = AuthService()
