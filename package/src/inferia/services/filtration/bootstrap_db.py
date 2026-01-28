import asyncio
import logging
import sys
import os

# Ensure current directory is in path (though python does this by default for scripts)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from db.database import DATABASE_URL, Base

# Import all models to ensure they are registered with Base
from db import models 
from rbac.initialization import initialize_default_org

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def bootstrap():
    logger.info("Connecting to database...")
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set!")
        return

    engine = create_async_engine(DATABASE_URL, echo=True, future=True)
    
    async with engine.begin() as conn:
        logger.info("Dropping all tables to ensure clean schema (init)...")
        await conn.run_sync(Base.metadata.drop_all)
        logger.info("Creating tables...")
        await conn.run_sync(Base.metadata.create_all)

    AsyncSessionLocal = async_sessionmaker(
        engine,
        expire_on_commit=False
    )
    
    async with AsyncSessionLocal() as session:
        logger.info("Initializing default Organization and Superadmin...")
        await initialize_default_org(session)
        
    await engine.dispose()
    logger.info("Bootstrap complete.")

if __name__ == "__main__":
    asyncio.run(bootstrap())
