"""Application configuration using pydantic-settings."""

import sys
from dataclasses import dataclass
from functools import lru_cache

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class FeatureGroup:
    """The SRAM group that grants a feature.

    Attributes:
        short_name: The group's short name, as it appears in the entitlement.
        collaboration: The global URN of the collaboration whose group grants the
            feature. None when the configuration named no collaboration, in which case
            the feature grants nothing.
    """

    short_name: str
    collaboration: str | None = None


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

    # SRAM token introspection (for application tokens)
    sram_introspection_token: str | None = None
    sram_introspection_url: str = "https://sram.surf.nl/api/tokens/introspect"

    # SRAM organisation API (collaboration management)
    sram_api_base_url: str = "https://sram.surf.nl"
    sram_organisation_api_token: str | None = None
    sram_service_entity_id: str | None = None
    collaboration_manager_entitlement: str | None = None
    # Deleting a collaboration destroys its memberships and cannot be undone,
    # so it stays off unless a deployment asks for it
    collaboration_deletion_enabled: bool = False
    # Features this application offers, mapped to the SRAM groups that grant them, as
    # "feature=short_name" or "feature=collaboration_urn/short_name" pairs. A bare name
    # is both feature and short name.
    sram_feature_groups: str = ""

    # Session settings
    session_cookie_name: str = "session"
    session_max_age: int = 3600  # 1 hour
    session_https_only: bool = True  # Use secure cookies (required for HTTPS)

    # Server settings
    base_url: str = "http://localhost:8124"
    allowed_redirect_urls: list[str] = ["http://localhost:8124"]

    @property
    def feature_groups(self) -> dict[str, "FeatureGroup"]:
        """Features mapped to the groups that grant them.

        Each entry is ``feature=collaboration_urn/short_name``, binding the feature to
        one group in one collaboration. An entry naming only a short name parses, but
        grants nothing: see :func:`sram_fastapi.auth.grants_feature` for why a name on
        its own cannot be trusted.

        Returns:
            A mapping of feature name to the group granting it, empty when none are
            configured.
        """
        mapping: dict[str, FeatureGroup] = {}
        for entry in self.sram_feature_groups.split(","):
            entry = entry.strip()
            if not entry:
                continue
            feature, _, value = entry.partition("=")
            feature = feature.strip()
            value = value.strip() or feature
            collaboration, separator, short_name = value.rpartition("/")
            mapping[feature] = FeatureGroup(
                short_name=short_name.strip(),
                collaboration=collaboration.strip() if separator else None,
            )
        return mapping


class ConfigurationError(Exception):
    """Raised when required configuration is missing."""


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
