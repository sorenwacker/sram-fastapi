"""Application configuration using pydantic-settings."""

import sys
from functools import lru_cache

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application settings
    app_name: str = "SRAM FastAPI"
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # SRAM OIDC settings
    sram_oidc_client_id: str
    sram_oidc_client_secret: str
    sram_oidc_discovery_url: str = "https://proxy.sram.surf.nl/.well-known/openid-configuration"

    # Session settings
    session_cookie_name: str = "session"
    session_max_age: int = 3600  # 1 hour

    # Server settings
    base_url: str = "http://localhost:8124"
    allowed_redirect_urls: list[str] = ["http://localhost:8124"]


class ConfigurationError(Exception):
    """Raised when required configuration is missing."""

    pass


def _format_missing_settings(error: ValidationError) -> str:
    """Format validation errors into a readable message."""
    missing = []
    for err in error.errors():
        if err["type"] == "missing":
            field = err["loc"][0]
            env_var = field.upper()
            missing.append(f"  - {env_var}")

    if not missing:
        return str(error)

    return (
        "Missing required environment variables:\n"
        + "\n".join(missing)
        + "\n\n"
        + "Create a .env file or set these environment variables.\n"
        + "See .env.example for reference."
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    try:
        return Settings()
    except ValidationError as e:
        message = _format_missing_settings(e)
        print(f"\nConfiguration Error:\n{message}\n", file=sys.stderr)
        raise ConfigurationError(message) from e
