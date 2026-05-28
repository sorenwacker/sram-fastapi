"""Tests for authentication module."""

import pytest

from sram_fastapi.auth import IntrospectionTokenError, User
from sram_fastapi.config import ConfigurationError, Settings, get_settings


class TestUser:
    """Tests for User dataclass."""

    def test_user_from_minimal_claims(self):
        """User can be created with minimal claims."""
        claims = {"sub": "user123"}
        user = User.from_claims(claims)

        assert user.sub == "user123"
        assert user.email is None
        assert user.name is None

    def test_user_from_full_claims(self):
        """User extracts all available claims."""
        claims = {
            "sub": "user123",
            "email": "user@example.com",
            "name": "Test User",
            "preferred_username": "testuser",
            "eduperson_entitlement": ["urn:example:entitlement"],
            "voperson_external_affiliation": ["staff@tudelft.nl"],
        }
        user = User.from_claims(claims)

        assert user.sub == "user123"
        assert user.email == "user@example.com"
        assert user.name == "Test User"
        assert user.preferred_username == "testuser"
        assert user.eduperson_entitlement == ["urn:example:entitlement"]
        assert user.voperson_external_affiliation == ["staff@tudelft.nl"]
        assert user.raw_claims == claims

    def test_user_preserves_raw_claims(self):
        """User preserves all raw claims including unknown ones."""
        claims = {
            "sub": "user123",
            "custom_claim": "custom_value",
        }
        user = User.from_claims(claims)

        assert user.raw_claims["custom_claim"] == "custom_value"


class TestSettings:
    """Tests for Settings configuration."""

    def test_settings_with_required_fields(self, monkeypatch):
        """Settings can be created with required environment variables."""
        monkeypatch.setenv("SRAM_OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("SRAM_OIDC_CLIENT_SECRET", "test-secret")

        settings = Settings()

        assert settings.sram_oidc_client_id == "test-client"
        assert settings.sram_oidc_client_secret == "test-secret"

    def test_settings_defaults(self, monkeypatch):
        """Settings have sensible defaults."""
        monkeypatch.setenv("SRAM_OIDC_CLIENT_ID", "test-client")
        monkeypatch.setenv("SRAM_OIDC_CLIENT_SECRET", "test-secret")

        settings = Settings()

        assert settings.debug is False
        assert settings.session_max_age == 3600
        assert "proxy.sram.surf.nl" in settings.sram_oidc_discovery_url

    def test_settings_missing_required_raises(self, monkeypatch, tmp_path):
        """Settings raises error when required fields are missing."""
        monkeypatch.delenv("SRAM_OIDC_CLIENT_ID", raising=False)
        monkeypatch.delenv("SRAM_OIDC_CLIENT_SECRET", raising=False)
        monkeypatch.chdir(tmp_path)  # Use empty dir without .env

        with pytest.raises(Exception):  # ValidationError
            Settings()

    def test_get_settings_missing_shows_helpful_error(self, monkeypatch, tmp_path):
        """get_settings raises ConfigurationError with helpful message."""
        monkeypatch.delenv("SRAM_OIDC_CLIENT_ID", raising=False)
        monkeypatch.delenv("SRAM_OIDC_CLIENT_SECRET", raising=False)
        monkeypatch.chdir(tmp_path)  # Use empty dir without .env
        get_settings.cache_clear()

        with pytest.raises(ConfigurationError) as exc_info:
            get_settings()

        error_message = str(exc_info.value)
        assert "SRAM_OIDC_CLIENT_ID" in error_message
        assert "SRAM_OIDC_CLIENT_SECRET" in error_message
        assert ".env" in error_message


class TestIntrospectionTokenError:
    """Tests for IntrospectionTokenError exception."""

    def test_default_message(self):
        """Error has default message."""
        error = IntrospectionTokenError()
        assert "invalid or expired" in str(error)

    def test_custom_message(self):
        """Error accepts custom message."""
        error = IntrospectionTokenError("Custom error message")
        assert str(error) == "Custom error message"
        assert error.message == "Custom error message"

    def test_is_exception(self):
        """Error can be raised and caught."""
        with pytest.raises(IntrospectionTokenError):
            raise IntrospectionTokenError("Test error")
