import asyncio
import logging
import sys
import os
import argparse

# Ensure current directory is in path (though python does this by default for scripts)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from inferia.services.api_gateway.db.database import DATABASE_URL, Base

# Import all models to ensure they are registered with Base
from db import models 
from inferia.services.api_gateway.rbac.initialization import initialize_default_org

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def bootstrap(reset: bool = False):
    logger.info("Connecting to database...")
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set!")
        return

    engine = create_async_engine(DATABASE_URL, echo=True, future=True)

    async with engine.begin() as conn:
        if reset:
            logger.warning("Reset mode enabled: dropping all API Gateway tables before recreate.")
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Ensuring API Gateway tables exist...")
        await conn.run_sync(Base.metadata.create_all)

    AsyncSessionLocal = async_sessionmaker(
        engine,
        expire_on_commit=False
    )
    
    async with AsyncSessionLocal() as session:
        logger.info("Initializing default Organization and Superadmin...")
        await initialize_default_org(session)
        
    await engine.dispose()
    logger.info("Bootstrap complete (roles normalized and defaults ensured).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap API Gateway DB state.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate API Gateway tables before bootstrapping.",
    )
    args = parser.parse_args()
    asyncio.run(bootstrap(reset=args.reset))
