"""Tests for the service token checks, the push log and the Renew login button."""

import httpx
import pytest
from fastapi.testclient import TestClient

from sram_fastapi.auth import User, get_optional_user
from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app
from sram_fastapi.token_checks import TokenChecker, get_token_checker

MANAGER = "urn:mace:surf.nl:sram:group:uniharderwijk:managers"


def settings(**tokens) -> Settings:
    """Settings with the given service tokens."""
    return Settings(
        _env_file=None,
        secret_key="test-secret-key-at-least-32-characters",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_api_base_url="https://sram.example.org",
        sram_introspection_url="https://sram.example.org/api/tokens/introspect",
        collaboration_manager_entitlement=MANAGER,
        base_url="http://testserver",
        **tokens,
    )


def sram(status: int = 200, body: dict | None = None, seen: list | None = None):
    """A fake SRAM answering every request with one status and body."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status == 0:
            raise httpx.ConnectError("down")
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler)


ALL = {
    "sram_scim_token": "pull",
    "sram_introspection_token": "intro",
    "sram_organisation_api_token": "org",
}


class TestTokenChecker:
    """One request per token, classified into valid, rejected, unreachable, not-configured."""

    async def test_pull_valid(self):
        seen: list[httpx.Request] = []
        status = await TokenChecker(settings(**ALL), sram(seen=seen)).check("pull")
        assert status == "valid"
        assert seen[0].method == "GET"
        assert seen[0].url.path == "/api/scim/v2/Users"
        assert seen[0].headers["Authorization"] == "Bearer pull"

    async def test_introspection_valid_when_inactive(self):
        seen: list[httpx.Request] = []
        checker = TokenChecker(settings(**ALL), sram(body={"active": False}, seen=seen))
        assert await checker.check("introspection") == "valid"
        assert seen[0].method == "POST"
        assert seen[0].url.path == "/api/tokens/introspect"
        assert seen[0].headers["Authorization"] == "Bearer intro"

    async def test_organisation_valid(self):
        seen: list[httpx.Request] = []
        assert await TokenChecker(settings(**ALL), sram(seen=seen)).check("organisation") == "valid"
        assert seen[0].url.path == "/api/organisations/v1"

    @pytest.mark.parametrize("name", ["pull", "introspection", "organisation"])
    @pytest.mark.parametrize("code", [401, 403])
    async def test_rejected(self, name, code):
        assert await TokenChecker(settings(**ALL), sram(status=code)).check(name) == "rejected"

    @pytest.mark.parametrize("name", ["pull", "introspection", "organisation"])
    @pytest.mark.parametrize("code", [0, 503])
    async def test_unreachable(self, name, code):
        assert await TokenChecker(settings(**ALL), sram(status=code)).check(name) == "unreachable"

    @pytest.mark.parametrize("name", ["pull", "introspection", "organisation"])
    async def test_not_configured_sends_nothing(self, name):
        seen: list[httpx.Request] = []
        assert await TokenChecker(settings(), sram(seen=seen)).check(name) == "not-configured"
        assert seen == []


def client(user: User | None, transport=None, **tokens) -> TestClient:
    """The demo app as the given user, with token checks against a fake SRAM."""
    s = settings(**tokens)
    app = create_demo_app(s)
    app.dependency_overrides[get_optional_user] = lambda: user
    app.dependency_overrides[get_token_checker] = lambda: TokenChecker(s, transport or sram())
    return TestClient(app, follow_redirects=False)


def user(*entitlements: str) -> User:
    return User.from_claims(
        {"sub": "s", "voperson_id": "v@sram.surf.nl", "eduperson_entitlement": list(entitlements)}
    )


class TestCheckRoute:
    """POST /scim/tokens/<name>/check."""

    def test_manager_gets_status(self):
        response = client(user(MANAGER), **ALL).post("/scim/tokens/pull/check")
        assert response.status_code == 200
        assert response.json() == {"token": "pull", "status": "valid"}

    def test_requires_manager(self):
        response = client(user("urn:x"), **ALL).post(
            "/scim/tokens/pull/check", headers={"Accept": "application/json"}
        )
        assert response.status_code == 403

    def test_requires_login(self):
        assert client(None, **ALL).post("/scim/tokens/pull/check").status_code == 401

    def test_unknown_token(self):
        assert client(user(MANAGER), **ALL).post("/scim/tokens/push/check").status_code == 422

    def test_page_shows_check_buttons_and_push_log(self):
        c = client(user(MANAGER), sram_scim_push_token="push-token")
        c.get("/scim/v2/Users", headers={"Authorization": "Bearer wrong"})
        c.get("/scim/v2/Users", headers={"Authorization": "Bearer push-token"})
        text = c.get("/scim").text
        assert "Service tokens" in text
        for name in ("pull", "introspection", "organisation"):
            assert f"/scim/tokens/{name}/check" in text
        assert "Last push accepted" in text
        assert "Last push refused" in text
        assert "never" not in text.split("Last push accepted")[1].split("</tr>")[0]
        assert "never" not in text.split("Last push refused")[1].split("</tr>")[0]


class TestPushLog:
    """Push requests are recorded as accepted or refused."""

    def test_records_times(self):
        c = client(None, sram_scim_push_token="push-token")
        log = c.app.state
        c.get("/scim/v2/Users", headers={"Authorization": "Bearer push-token"})
        assert log.scim_push_log.last_accepted is not None
        assert log.scim_push_log.last_refused is None
        c.get("/scim/v2/Users")
        assert log.scim_push_log.last_refused is not None


class TestRenewLogin:
    """The navigation offers a renewed login to logged-in users only."""

    def test_logged_in_sees_button(self):
        text = client(user()).get("/").text
        assert "Renew login" in text

    def test_anonymous_does_not(self):
        assert "Renew login" not in client(None).get("/").text
