"""Pytest configuration and fixtures."""

import pytest
from fastapi.testclient import TestClient

from sram_fastapi.config import Settings
from sram_fastapi.main import create_app


@pytest.fixture(autouse=True)
def session_key(monkeypatch):
    """Provide the required SECRET_KEY to every test that builds Settings."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-at-least-32-characters")


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings with mock values."""
    return Settings(
        app_name="Test SRAM FastAPI",
        debug=True,
        secret_key="test-secret-key-at-least-32-characters",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_oidc_discovery_url="https://connect.surfconext.nl/.well-known/openid-configuration",
        base_url="http://testserver",
    )


@pytest.fixture
def app(test_settings: Settings):
    """Create test application."""
    return create_app(test_settings)


@pytest.fixture
def client(app) -> TestClient:
    """Create test client."""
    return TestClient(app)
