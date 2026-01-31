import asyncio
import logging
import sys
from pathlib import Path
from sqlalchemy import text

# Adjust path to find modules
sys.path.append(str(Path(__file__).parent))

from db.database import engine, Base
# Import all DB models to register them with Base.metadata
from db.models import (
    user, organization, user_organization, role, invitation, 
    api_key, audit_log, deployment, inference_log, policy, usage
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def drop_all():
    logger.info("Connecting to database to wipe schema...")
    async with engine.begin() as conn:
        logger.warning("DROPPING SCHEMA public CASCADE...")
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO inferia;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
    logger.info("Schema wiped and recreated successfully.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(drop_all())
