"""Tests for main application endpoints."""

from fastapi.testclient import TestClient


class TestPublicEndpoints:
    """Tests for public endpoints."""

    def test_root_unauthenticated(self, client: TestClient):
        """Root endpoint returns welcome message when not authenticated."""
        response = client.get("/")
        assert response.status_code == 200

        data = response.json()
        assert data["authenticated"] is False
        assert "login_url" in data

    def test_health_check(self, client: TestClient):
        """Health endpoint returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestAuthEndpoints:
    """Tests for authentication endpoints."""

    def test_login_redirects(self, client: TestClient):
        """Login endpoint redirects to SRAM."""
        response = client.get("/auth/login", follow_redirects=False)
        # Should redirect to SRAM authorization URL
        assert response.status_code == 307 or response.status_code == 302

    def test_logout_clears_session(self, client: TestClient):
        """Logout endpoint redirects to root."""
        response = client.get("/auth/logout", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers.get("location") == "/"

    def test_me_requires_authentication(self, client: TestClient):
        """Me endpoint returns 401 when not authenticated."""
        response = client.get("/auth/me")
        assert response.status_code == 401


class TestProtectedAPI:
    """Tests for protected API endpoints."""

    def test_protected_api_requires_token(self, client: TestClient):
        """Protected API returns 401 without token."""
        response = client.get("/api/protected")
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers

    def test_protected_api_rejects_invalid_token(self, client: TestClient):
        """Protected API returns 401 with invalid token."""
        response = client.get(
            "/api/protected",
            headers={"Authorization": "Bearer invalid-token"},
        )
        # Will fail because we can't introspect without real SRAM connection
        # In a real test, we'd mock the introspection endpoint
        assert response.status_code == 401
