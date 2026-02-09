import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from db.database import DATABASE_URL, Base

# Import all models explicitly to ensure they are registered with SQLAlchemy
from db.models import (
    Organization,
    User,
    Deployment,
    Policy,
    ApiKey,
    Usage,
    Role,
    InferenceLog,
    Invitation,
    UserOrganization,
    AuditLog,
    SystemSetting,
)
from db.models.role import Role

logger = logging.getLogger(__name__)


async def reset_db():
    logger.info("Connecting to database...")
    engine = create_async_engine(DATABASE_URL, echo=True, future=True)

    async with engine.begin() as conn:
        logger.info("Dropping all tables...")
        await conn.run_sync(Base.metadata.drop_all)
        logger.info("Creating all tables...")
        await conn.run_sync(Base.metadata.create_all)

    # Seed Data
    async with engine.begin() as conn:
        # We need a session for adding data, or we can use core insert
        pass

    # Re-connect for session operations
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.ext.asyncio import AsyncSession

    AsyncSessionLocal = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as session:
        logger.info("Seeding Roles...")

        # Admin Role
        admin_permissions = [
            # Core
            "admin:all",
            # API Keys
            "api_key:create",
            "api_key:list",
            "api_key:revoke",
            # Deployments
            "deployment:create",
            "deployment:list",
            "deployment:update",
            "deployment:delete",
            # Prompt Templates
            "prompt_template:create",
            "prompt_template:list",
            "prompt_template:delete",
            # Member Management
            "member:invite",
            "member:delete",
            "member:list",
            "role:update",
            # Models
            "model:access",
            # Knowledge Base
            "knowledge_base:create",
            "knowledge_base:add_data",
            "knowledge_base:delete",
            "knowledge_base:list",
        ]

        member_permissions = [
            "model:access",
            "deployment:list",
            "prompt_template:list",
            "member:list",
            "knowledge_base:list",
        ]

        admin_role = Role(
            name="admin",
            description="Administrator with full access",
            permissions=admin_permissions,
        )
        member_role = Role(
            name="member",
            description="Regular member with limited access",
            permissions=member_permissions,
        )

        session.add(admin_role)
        session.add(member_role)
        await session.commit()
        logger.info("Roles seeded successfully.")

    await engine.dispose()
    logger.info("Database reset complete.")


if __name__ == "__main__":
    asyncio.run(reset_db())
