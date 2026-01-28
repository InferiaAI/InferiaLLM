"""
Configuration for Inference Gateway.
"""

from typing import Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Inference Gateway settings."""
    
    # Application Settings
    app_name: str = "InferiaLLM Inference Gateway"
    app_version: str = "0.1.0"
    environment: str = "development"
    
    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8001
    reload: bool = False
    log_level: str = "INFO"
    
    # Filtration Gateway Settings
    filtration_gateway_url: str = "http://localhost:8000"
    filtration_internal_key: str = Field(
        default="dev-internal-key-change-in-prod", 
        alias="INTERNAL_API_KEY",
        validation_alias="INTERNAL_API_KEY"
    )
    
    # Nosana Authentication
    nosana_internal_api_key: str = ""
    
    # Timeouts
    request_timeout: int = 30
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # Ignore extra env vars from shared .env file
    )

    def model_post_init(self, __context: Any) -> None:
        """Load local configuration override if exists."""
        import json
        from pathlib import Path
        from typing import Dict, Any

        config_path = Path.home() / ".inferia" / "config.json"
        
        # Helper to recursively update Pydantic models from dict
        # Since this class defines fields directly, simple attribute setting works
        # But we need to traverse the nested 'providers' -> 'depin' -> 'nosana' structure 
        # to map it to 'nosana_internal_api_key' if we want automagic.
        #
        # However, the user config structure is:
        # { "providers": { "depin": { "nosana": { "wallet_private_key": "..." } } } }
        # 
        # But this service uses `nosana_internal_api_key` which is DIFFERENT from wallet key.
        # Usually internal key is server-to-server auth.
        #
        # If the user meant "Orchestration" as in "Orchestrator logic", 
        # it seems it DOES rely on Filtration for everything else.
        #
        # I will implement the generic loader anyway.

        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    local_config = json.load(f)
                
                # Check for direct overrides of top-level fields
                # Also support loading from the nested 'providers' if relevant fields existed here
                
                for key, value in local_config.items():
                    if hasattr(self, key):
                        setattr(self, key, value)
                        
            except Exception as e:
                print(f"Failed to load local config: {e}")


settings = Settings()
