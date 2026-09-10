"""Tests for demo application."""

import re

import pytest
from fastapi.testclient import TestClient

from sram_fastapi.auth import get_oidc_client
from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app

INTROSPECTION_RESULT = {
    "active": True,
    "status": "token-valid",
    "client_id": "https://sram-demo.example.org",
    "sub": "abc@sram.surf.nl",
    "username": "jdoe",
    "iat": 1_700_000_000,
    "exp": 1_700_000_300,
    "aud": "https://sram-demo.example.org",
    "iss": "https://sram.surf.nl",
    "user": {
        "name": "Jane Doe",
        "given_name": "Jane",
        "family_name": "Doe",
        "email": "jane@example.org",
        "sub": "abc@sram.surf.nl",
        "uid": "abc@sram.surf.nl",
        "username": "jdoe",
        "voperson_external_id": "jdoe@tudelft.nl",
        "voperson_external_affiliation": "employee@tudelft.nl",
        "eduperson_entitlement": [
            "urn:mace:surf.nl:sram:group:tudelft:sramdemo",
            "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemogroup",
            "urn:mace:surf.nl:sram:group:tudelft:sramdemo:group1",
        ],
    },
}


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

    def test_entitlement_protected_requires_auth(self, demo_client: TestClient):
        """Entitlement-protected endpoint requires authentication."""
        response = demo_client.get("/demo/entitlement-protected")
        assert response.status_code == 401

    def test_affiliation_protected_requires_auth(self, demo_client: TestClient):
        """Affiliation-protected endpoint requires authentication."""
        response = demo_client.get("/demo/affiliation-protected")
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

    def test_test_token_page_renders(self, demo_client: TestClient):
        """Test token page renders for unauthenticated users."""
        response = demo_client.get("/test-token")
        assert response.status_code == 200
        assert "Test SRAM Token" in response.text

    def test_test_token_validate_without_config(self, demo_client: TestClient):
        """Token validation returns error when introspection not configured."""
        response = demo_client.post(
            "/test-token/validate",
            json={"token": "some-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["status"] == "not-configured"

    def test_test_token_validate_empty_token(self, demo_client: TestClient):
        """Token validation returns error for empty token."""
        response = demo_client.post(
            "/test-token/validate",
            json={"token": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["status"] == "token-missing"

    def test_hello_requires_token(self, demo_client: TestClient):
        """Hello endpoint requires Bearer token."""
        response = demo_client.get("/api/hello")
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers

    def test_hello_returns_complete_introspection(self, demo_settings: Settings):
        """A valid token yields the whole introspection answer, not just name and email."""
        settings = demo_settings.model_copy(update={"sram_introspection_token": "service-token"})
        app = create_demo_app(settings)

        class FakeOIDCClient:
            async def introspect_sram_token(self, token: str) -> dict:
                return INTROSPECTION_RESULT

        app.dependency_overrides[get_oidc_client] = lambda: FakeOIDCClient()
        response = TestClient(app).get("/api/hello", headers={"Authorization": "Bearer t"})

        assert response.status_code == 200
        data = response.json()
        assert data["user"] == "Jane Doe"
        assert data["email"] == "jane@example.org"
        assert data["introspection"] == INTROSPECTION_RESULT
        assert data["collaborations"] == ["tudelft:sramdemo"]
        assert data["groups"] == ["tudelft:sramdemo/group1", "tudelft:sramdemo/sramdemogroup"]

    def test_test_token_page_renders_introspection_fields(self, demo_client: TestClient):
        """The token test page names the fields it renders from the hello response."""
        response = demo_client.get("/test-token")
        assert response.status_code == 200
        for marker in ("data.introspection", "data.collaborations", "data.groups"):
            assert marker in response.text

    def test_test_token_page_escapes_every_rendered_value(self, demo_client: TestClient):
        """Every value the page inserts into HTML goes through the escaping helpers.

        The page builds HTML with template literals. An interpolation is safe when it is an
        escaped value, a fragment named as already escaped, the output of a helper that
        escapes its inputs, or a number the page computed itself. Values that only reach
        textContent or a request header are safe too.
        """
        allowed_prefixes = (
            "escapeHtml(",
            "escaped",
            "statsHtml",
            "rowsTable(",
            "urnList(",
            "body",
            "colorClass",
            "rating",
            "ms",
            "token",
        )
        script = demo_client.get("/test-token").text.split("<script>", 1)[1].split("</script>")[0]
        interpolations = re.findall(r"\$\{([^}]*)\}", script)
        assert interpolations
        offending = [i for i in interpolations if not i.strip().startswith(allowed_prefixes)]
        assert offending == []

    def test_hello_rejects_invalid_token(self, demo_client: TestClient):
        """Hello endpoint rejects request when introspection not configured."""
        response = demo_client.get(
            "/api/hello",
            headers={"Authorization": "Bearer some-token"},
        )
        assert response.status_code == 500
        assert "not configured" in response.json()["detail"]
