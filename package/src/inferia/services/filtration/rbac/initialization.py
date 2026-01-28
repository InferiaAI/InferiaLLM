import logging
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Organization, User, UserOrganization, Role
from config import settings
from rbac.auth import auth_service
from schemas.auth import PermissionEnum

logger = logging.getLogger(__name__)

async def initialize_default_org(db: AsyncSession):
    """
    Initialize the default organization, roles, permissions, and superadmin if they don't exist.
    """
    try:
        # 1. Initialize Roles & Permissions
        all_permissions = [p.value for p in PermissionEnum]
        
        # Admin Role: All permissions
        admin_role_stmt = select(Role).where(Role.name == "admin")
        admin_role_res = await db.execute(admin_role_stmt)
        admin_role = admin_role_res.scalars().first()
        
        if not admin_role:
            logger.info("Creating 'admin' role with all permissions")
            admin_role = Role(name="admin", description="Administrator with full access", permissions=all_permissions)
            db.add(admin_role)
        else:
            # Update permissions if needed (e.g. new permissions added to enum)
            logger.info("Updating 'admin' role permissions")
            admin_role.permissions = all_permissions
            db.add(admin_role)

        # Member Role: Limited permissions
        member_permissions = [
            PermissionEnum.DEPLOYMENT_LIST.value,
            PermissionEnum.MODEL_ACCESS.value,
            PermissionEnum.PROMPT_LIST.value,
            PermissionEnum.PROMPT_VIEW.value,
            PermissionEnum.KB_LIST.value,
            PermissionEnum.KB_VIEW.value,
            PermissionEnum.KB_ADD_DATA.value,
            PermissionEnum.ORG_VIEW.value,
            PermissionEnum.USER_LIST.value, # Members can usually see other members
            PermissionEnum.USER_VIEW.value,
        ]
        
        member_role_stmt = select(Role).where(Role.name == "member")
        member_role_res = await db.execute(member_role_stmt)
        member_role = member_role_res.scalars().first()
        
        if not member_role:
            logger.info("Creating 'member' role")
            member_role = Role(name="member", description="Standard organization member", permissions=member_permissions)
            db.add(member_role)
        
        await db.commit()

        # 2. Check/Create Default Organization
        stmt = select(Organization).limit(1)
        result = await db.execute(stmt)
        org = result.scalars().first()

        if not org:
            logger.info(f"No organization found. Creating default organization: {settings.default_org_name}")
            org = Organization(name=settings.default_org_name, log_payloads=True)
            db.add(org)
            await db.commit()
            await db.refresh(org)
        else:
            logger.info(f"Organization exists: {org.name}")

        target_org_id = org.id

        # 3. Check/Create Superadmin User
        stmt = select(User).where(User.email == settings.superadmin_email)
        result = await db.execute(stmt)
        admin_user = result.scalars().first()

        if not admin_user:
            logger.info(f"Superadmin not found. Creating user: {settings.superadmin_email}")
            password_hash = auth_service.get_password_hash(settings.superadmin_password)
            admin_user = User(
                email=settings.superadmin_email,
                password_hash=password_hash,
                default_org_id=target_org_id,
                totp_enabled=False 
            )
            db.add(admin_user)
            await db.commit()
            await db.refresh(admin_user)
        else:
             logger.info(f"Superadmin user exists: {admin_user.email}")
             if not admin_user.default_org_id:
                 admin_user.default_org_id = target_org_id
                 db.add(admin_user)
                 await db.commit()


        # 4. Ensure User is in the Organization with 'admin' role
        stmt = select(UserOrganization).where(
            UserOrganization.user_id == admin_user.id,
            UserOrganization.org_id == target_org_id
        )
        result = await db.execute(stmt)
        user_org = result.scalars().first()

        if not user_org:
            logger.info(f"Adding superadmin to organization {org.name} as admin")
            user_org = UserOrganization(
                user_id=admin_user.id,
                org_id=target_org_id,
                role="admin"
            )
            db.add(user_org)
            await db.commit()
        elif user_org.role != "admin":
             logger.info(f"Updating superadmin role to 'admin' in {org.name}")
             user_org.role = "admin"
             db.add(user_org)
             await db.commit()

    except Exception as e:
        logger.error(f"Failed to initialize default organization: {e}")
