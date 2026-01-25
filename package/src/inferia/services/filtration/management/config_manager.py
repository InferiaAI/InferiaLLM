import asyncio
import logging
from typing import Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from config import settings, ProvidersConfig
from db.models import SystemSetting
from db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

CONFIG_KEY = "providers_config"

class ConfigManager:
    """
    Manages loading and polling of system configuration from the database.
    Replaces the file-based configuration system.
    """
    
    _instance = None
    
    def __init__(self):
        self._polling_active = False
        self._task = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ConfigManager()
        return cls._instance

    async def save_config(self, db: AsyncSession, config: Dict[str, Any]):
        """Save provider configuration to the database."""
        stmt = select(SystemSetting).where(SystemSetting.key == CONFIG_KEY)
        result = await db.execute(stmt)
        setting = result.scalars().first()
        
        if setting:
            setting.value = config
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(setting, "value")
        else:
            setting = SystemSetting(key=CONFIG_KEY, value=config)
            db.add(setting)
            
        await db.commit()
        await db.refresh(setting)
        
        # Update local instance immediately
        self._update_local_settings(config)
        logger.info("Configuration saved to database and local settings updated.")

    async def load_config(self, db: AsyncSession) -> Optional[Dict[str, Any]]:
        """Load provider configuration from the database."""
        stmt = select(SystemSetting).where(SystemSetting.key == CONFIG_KEY)
        result = await db.execute(stmt)
        setting = result.scalars().first()
        
        if setting:
            return setting.value
        return None

    def _update_local_settings(self, config_data: Dict[str, Any]):
        """Recursively update the Pydantic settings object."""
        if not config_data:
            return

        def update_model(model: BaseModel, data: Dict[str, Any]):
            for key, value in data.items():
                if isinstance(value, dict) and hasattr(model, key):
                    sub_model = getattr(model, key)
                    if isinstance(sub_model, BaseModel):
                        update_model(sub_model, value)
                    else:
                        setattr(model, key, value)
                elif hasattr(model, key):
                    setattr(model, key, value)

        if "providers" in config_data:
             update_model(settings.providers, config_data["providers"])
        else:
            # Fallback/Direct update if structure matches
            update_model(settings.providers, config_data)
            
        logger.debug("Local settings refreshed from source.")

    async def _poll_loop(self):
        """Background task to poll for config changes."""
        logger.info("Starting configuration polling loop...")
        while self._polling_active:
            try:
                async with AsyncSessionLocal() as db:
                    config = await self.load_config(db)
                    if config:
                         self._update_local_settings(config)
            except Exception as e:
                logger.error(f"Error polling configuration: {e}")
            
            await asyncio.sleep(10) # Poll every 10 seconds

    def start_polling(self):
        """Start the background polling task."""
        if self._polling_active:
            return
        
        self._polling_active = True
        self._task = asyncio.create_task(self._poll_loop())
        
    def stop_polling(self):
        """Stop the background polling task."""
        self._polling_active = False
        if self._task:
            self._task.cancel()

config_manager = ConfigManager.get_instance()
