import logging
from typing import Dict, Any, Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from inferia.services.api_gateway.config import settings
from inferia.services.api_gateway.db.models import SystemSetting
from inferia.services.api_gateway.db.database import AsyncSessionLocal
from inferia.common.config_manager import BaseConfigManager, update_pydantic_model

logger = logging.getLogger(__name__)

CONFIG_KEY = "providers_config"


class ConfigManager(BaseConfigManager):
    """
    Manages loading and polling of system configuration from the database.
    Replaces the file-based configuration system.
    """

    _instance = None

    def __init__(self):
        super().__init__(poll_interval=10)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ConfigManager()
        return cls._instance

    async def poll_once(self):
        """Perform a single poll of the database."""
        async with AsyncSessionLocal() as db:
            config = await self.load_config(db)
            if config:
                self._update_local_settings(config)

    async def save_config(self, db: AsyncSession, config: Dict[str, Any]):
        """Save provider configuration to the database with merging."""
        stmt = select(SystemSetting).where(SystemSetting.key == CONFIG_KEY)
        result = await db.execute(stmt)
        setting = result.scalars().first()

        if setting:
            # Merge new config into existing one to preserve unmasked secrets
            existing_config = setting.value
            merged_config = self._merge_configs(existing_config, config)
            setting.value = merged_config
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(setting, "value")
            final_config = merged_config
        else:
            setting = SystemSetting(key=CONFIG_KEY, value=config)
            db.add(setting)
            final_config = config

        await db.commit()
        await db.refresh(setting)

        # Update local instance immediately
        self._update_local_settings(final_config)
        logger.info("Configuration saved to database and local settings updated.")

    def _mask_secret(self, value: Optional[str]) -> Optional[str]:
        if not value or len(value) < 8:
            return value
        return f"{value[:4]}...{value[-4:]}"

    def _merge_configs(
        self, existing: Dict[str, Any], new: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Recursively merge new config into existing, skipping masked values."""
        merged = existing.copy()
        for key, value in new.items():
            if (
                isinstance(value, dict)
                and key in merged
                and isinstance(merged[key], dict)
            ):
                merged[key] = self._merge_configs(merged[key], value)
            elif (
                isinstance(value, list)
                and key in merged
                and isinstance(merged[key], list)
            ):
                # Smart merging for credential lists to prevent deletion and masking corruption
                if key in ["api_keys", "wallets", "credentials"]:
                    merged[key] = self._merge_credential_lists(merged[key], value)
                else:
                    merged[key] = value
            else:
                # Skip if the new value is masked
                if value == "********":
                    continue
                if isinstance(value, str) and "..." in value:
                    # Potentially masked, check against existing
                    existing_val = merged.get(key)
                    if existing_val and isinstance(existing_val, str):
                        if value == self._mask_secret(existing_val):
                            continue
                merged[key] = value
        return merged

    def _merge_credential_lists(
        self, existing: List[Dict[str, Any]], new: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Smart merge for credential lists (api_keys, wallets, etc.).
        1. Preserves unmasked secrets if incoming value is masked.
        2. Preserves existing items if missing in new list (additive mode for safety).
        """
        # Dictionary for fast lookup by name (or name+provider for universal)
        def get_id(item: Dict[str, Any]) -> str:
            return f"{item.get('provider', '')}:{item.get('name', '')}"

        merged_map = {get_id(item): item.copy() for item in existing}

        for new_item in new:
            item_id = get_id(new_item)
            if item_id in merged_map:
                existing_item = merged_map[item_id]
                # Merge dict contents to handle masked keys/values
                for k, v in new_item.items():
                    # Handle specific secret keys: 'key' for nosana, 'value' for universal, 'mnemonic' for akash
                    if k in ["key", "value", "mnemonic"] and isinstance(v, str) and "..." in v:
                        existing_val = existing_item.get(k)
                        if existing_val and v == self._mask_secret(existing_val):
                            continue  # Skip updating this masked field
                    existing_item[k] = v
            else:
                # It's a brand new item in this list
                merged_map[item_id] = new_item.copy()

        return list(merged_map.values())

    async def initialize(self):
        """Initial load of configuration from database."""
        try:
            async with AsyncSessionLocal() as db:
                config = await self.load_config(db)
                if config:
                    self._update_local_settings(config)
                    logger.info("Initial configuration loaded from database.")
                else:
                    logger.info("No configuration found in database, using defaults.")
        except Exception as e:
            logger.error(f"Failed to load initial configuration: {e}")

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

        providers = config_data.get("providers", config_data)
        update_pydantic_model(settings.providers, providers)
        logger.debug("Local settings refreshed from database.")


config_manager = ConfigManager.get_instance()
