"""
Configuration for Inference Gateway.
"""

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
    from pydantic import Field
    filtration_internal_key: str = Field(
        "dev-internal-key-change-in-prod", alias="INTERNAL_API_KEY"
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


settings = Settings()
