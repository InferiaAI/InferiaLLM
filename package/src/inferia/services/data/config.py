"""
Data Service Configuration.
"""

from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChromaConfig(BaseModel):
    api_key: Optional[str] = None
    tenant: Optional[str] = None
    url: Optional[str] = None
    is_local: bool = True
    database: Optional[str] = None


class VectorDBConfig(BaseModel):
    chroma: ChromaConfig = Field(default_factory=ChromaConfig)


class ProvidersConfig(BaseModel):
    vectordb: VectorDBConfig = Field(default_factory=VectorDBConfig)


class Settings(BaseSettings):
    """Data Service Settings."""

    # Application Settings
    app_name: str = "InferiaLLM Data Service"
    app_version: str = "0.1.0"
    environment: str = "development"

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8003
    reload: bool = False
    log_level: str = "INFO"

    # CORS Settings
    allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:3001,http://localhost:5173,http://localhost:8001",
        validation_alias="ALLOWED_ORIGINS",
    )

    # Providers
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    # Redis for caching (optional)
    redis_url: str = "redis://localhost:6379/0"

    # Optional OpenAI Key for Rewriting
    openai_api_key: Optional[str] = None

    # Control Plane Connection
    api_gateway_url: str = Field(
        default="http://localhost:8000", validation_alias="API_GATEWAY_URL"
    )
    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
