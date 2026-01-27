"""
Orchestration Gateway Configuration

Settings loaded from environment variables with sensible defaults.
Updated to use Pydantic Settings and load from ~/.inferia/config.json
"""

import os
from typing import Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""
    
    # App info
    app_name: str = "Orchestration Gateway"
    app_version: str = "1.0.0"
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    
    # Server settings
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    http_port: int = Field(default=8080, validation_alias="HTTP_PORT")
    grpc_port: int = Field(default=50051, validation_alias="GRPC_PORT")
    
    # Database
    postgres_dsn: str = Field(
        default="postgresql://inferia:inferia@localhost:5432/inferia",
        validation_alias="DATABASE_URL" 
    )
    # Pydantic will check DATABASE_URL first. 
    # If using POSTGRES_DSN env var, explicit support could be added via alias_priority 
    # but Pydantic standardizes on one usually. We'll stick to typical pattern.

    # Redis
    redis_host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_username: str = Field(default="", validation_alias="REDIS_USERNAME")
    redis_password: str = Field(default="", validation_alias="REDIS_PASSWORD")
    
    # Nosana
    nosana_sidecar_url: str = Field(default="http://localhost:3000", validation_alias="NOSANA_SIDECAR_URL")
    nosana_internal_api_key: str = Field(default="nos-internal-secret-change-in-prod", validation_alias="NOSANA_INTERNAL_API_KEY")
    
    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    def __post_init__(self):
         # Pydantic uses model_post_init, but if we migrated from dataclass verify usages.
         pass
         
    def model_post_init(self, __context: Any) -> None:
        """Load local configuration override if exists."""
        import json
        from pathlib import Path
        
        # 1. Fix Postgres DSN asyncpg prefix if present (Pydantic validator alternative)
        if self.postgres_dsn.startswith("postgresql+asyncpg://"):
             # Orchestration might use sync or a different driver, 
             # preserving original logic which replaced it with standard postgres://
             self.postgres_dsn = self.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

        # 2. Load Local Config
        config_path = Path.home() / ".inferia" / "config.json"
        
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    local_config = json.load(f)
                
                for key, value in local_config.items():
                    if hasattr(self, key):
                        setattr(self, key, value)
            except Exception as e:
                print(f"Failed to load local config: {e}")


settings = Settings()
