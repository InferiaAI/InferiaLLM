"""UnifiedBaseSettings — drop-in BaseSettings that injects the yaml source.

Subclasses set `_yaml_path` (e.g. "services.api_gateway") to declare which
yaml sub-tree feeds them. The precedence chain is documented in Section 8.1
of the design spec.
"""
from typing import ClassVar, Optional, Tuple, Type
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .source import YamlConfigSettingsSource


class UnifiedBaseSettings(BaseSettings):
    """BaseSettings subclass that adds yaml between env and pydantic defaults."""

    _yaml_path: ClassVar[Optional[str]] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Order = highest precedence first:
        #   init (CLI), env, .env file, yaml, /run/secrets, pydantic defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
