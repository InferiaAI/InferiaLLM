import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from db.database import DATABASE_URL, Base
from db.models import * # Import all models to ensure they are registered
from db.models.role import Role

async def reset_db():
    print("Connecting to database...")
    engine = create_async_engine(DATABASE_URL, echo=True, future=True)
    
    async with engine.begin() as conn:
        print("Dropping all tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Creating all tables...")
        await conn.run_sync(Base.metadata.create_all)
        
    # Seed Data
    async with engine.begin() as conn:
        # We need a session for adding data, or we can use core insert
        pass

    # Re-connect for session operations
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.ext.asyncio import AsyncSession
    
    AsyncSessionLocal = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    async with AsyncSessionLocal() as session:
        print("Seeding Roles...")
        
        # Admin Role
        admin_permissions = [
            # Core
            "admin:all",
            # API Keys
            "api_key:create", "api_key:list", "api_key:revoke",
            # Deployments
            "deployment:create", "deployment:list", "deployment:update", "deployment:delete",
            # Prompt Templates
            "prompt_template:create", "prompt_template:list", "prompt_template:delete",
            # Member Management
            "member:invite", "member:delete", "member:list", "role:update",
            # Models
            "model:access",
            # Knowledge Base
            "knowledge_base:create", "knowledge_base:add_data", "knowledge_base:delete", "knowledge_base:list"
        ]
        
        member_permissions = [
            "model:access",
            "deployment:list",
            "prompt_template:list",
            "member:list",
            "knowledge_base:list"
        ]
        
        admin_role = Role(name="admin", description="Administrator with full access", permissions=admin_permissions)
        member_role = Role(name="member", description="Regular member with limited access", permissions=member_permissions)
        
        session.add(admin_role)
        session.add(member_role)
        await session.commit()
        print("Roles seeded successfully.")

    await engine.dispose()
    print("Database reset complete.")

if __name__ == "__main__":
    asyncio.run(reset_db())
