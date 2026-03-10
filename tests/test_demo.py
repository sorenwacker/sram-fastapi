"""Tests for demo application."""

import pytest
from fastapi.testclient import TestClient

from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app


@pytest.fixture
def demo_settings() -> Settings:
    """Create test settings for demo app."""
    return Settings(
        app_name="Test SRAM Demo",
        debug=True,
        secret_key="test-secret-key",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        base_url="http://testserver",
    )


@pytest.fixture
def demo_app(demo_settings: Settings):
    """Create demo test application."""
    return create_demo_app(demo_settings)


@pytest.fixture
def demo_client(demo_app) -> TestClient:
    """Create demo test client."""
    return TestClient(demo_app)


class TestDemoPages:
    """Tests for demo HTML pages."""

    def test_home_unauthenticated(self, demo_client: TestClient):
        """Home page renders for unauthenticated users."""
        response = demo_client.get("/")
        assert response.status_code == 200
        assert "SRAM Authentication Demo" in response.text
        assert "Login with SRAM" in response.text

    def test_profile_requires_auth(self, demo_client: TestClient):
        """Profile page requires authentication."""
        response = demo_client.get("/profile")
        assert response.status_code == 401

    def test_health_check(self, demo_client: TestClient):
        """Health endpoint works."""
        response = demo_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_login_redirects(self, demo_client: TestClient):
        """Login redirects to SRAM."""
        response = demo_client.get("/auth/login", follow_redirects=False)
        assert response.status_code in (302, 307)

    def test_logout_redirects_home(self, demo_client: TestClient):
        """Logout redirects to home."""
        response = demo_client.get("/auth/logout", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers.get("location") == "/"
