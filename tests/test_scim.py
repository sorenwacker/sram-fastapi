"""Tests for SCIM pull from SRAM and the /scim page."""

import httpx
import pytest
from fastapi.testclient import TestClient

from sram_fastapi.auth import User, get_optional_user
from sram_fastapi.collaborations import SRAMAPIError
from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app
from sram_fastapi.scim import (
    SCIMDirectory,
    SCIMNotConfiguredError,
    SCIMTokenError,
    SRAMSCIMClient,
    get_scim_client,
)

MANAGER = "urn:mace:surf.nl:sram:group:uniharderwijk:managers"

USERS = {
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "totalResults": 2,
    "Resources": [
        {
            "id": "u-1",
            "userName": "jdoe",
            "displayName": "Jane Doe",
            "emails": [{"value": "jdoe@example.org", "primary": True}],
            "urn:mace:surf.nl:sram:scim:extension:User": {"sramInactiveDays": 3},
        },
        {"id": "u-2", "userName": "rroe", "name": {"formatted": "Rick Roe"}},
    ],
}

GROUPS = {
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "totalResults": 1,
    "Resources": [
        {
            "id": "g-1",
            "displayName": "Cumulus group",
            "members": [{"value": "u-1"}, {"value": "u-2"}, {"value": "u-unknown"}],
            "urn:mace:surf.nl:sram:scim:extension:Group": {
                "urn": "uniharderwijk:cumulusgrp",
                "description": "Cloud research",
            },
        },
        {
            "id": "g-2",
            "displayName": "editors",
            "members": [{"value": "u-1"}],
            "urn:mace:surf.nl:sram:scim:extension:Group": {
                "urn": "uniharderwijk:cumulusgrp:editors"
            },
        },
    ],
}


def settings(token: str | None = "scim-token") -> Settings:
    """Settings with SCIM pull configured."""
    return Settings(
        _env_file=None,
        secret_key="test-secret-key-at-least-32-characters",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_scim_token=token,
        sram_api_base_url="https://sram.example.org",
        collaboration_manager_entitlement=MANAGER,
        base_url="http://testserver",
    )


def sram(status: int = 200, seen: list | None = None) -> httpx.MockTransport:
    """A fake SRAM serving the SCIM users and groups."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status)
        body = USERS if request.url.path.endswith("/Users") else GROUPS
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


class TestSCIMClient:
    """SCIM pull client."""

    async def test_reads_users_and_groups(self):
        """Users and groups are parsed, and group members resolved to users."""
        seen: list[httpx.Request] = []
        directory = await SRAMSCIMClient(settings(), sram(seen=seen)).get_directory()

        assert isinstance(directory, SCIMDirectory)
        assert [u.user_name for u in directory.users] == ["jdoe", "rroe"]
        jane = directory.users[0]
        assert jane.display_name == "Jane Doe"
        assert jane.email == "jdoe@example.org"
        assert directory.users[1].display_name == "Rick Roe"

        group = directory.groups[0]
        assert group.display_name == "Cumulus group"
        assert group.urn == "uniharderwijk:cumulusgrp"
        assert directory.member_names(group) == ["jdoe", "rroe", "u-unknown"]

        assert {r.url.path for r in seen} == {"/api/scim/v2/Users", "/api/scim/v2/Groups"}
        assert all(r.headers["Authorization"] == "Bearer scim-token" for r in seen)

    async def test_not_configured_sends_nothing(self):
        """Without a token no request reaches SRAM."""
        seen: list[httpx.Request] = []
        client = SRAMSCIMClient(settings(token=None), sram(seen=seen))
        assert client.configured is False
        with pytest.raises(SCIMNotConfiguredError):
            await client.get_directory()
        assert seen == []

    async def test_rejected_token(self):
        """A 401 from SRAM is reported as a token problem."""
        with pytest.raises(SCIMTokenError):
            await SRAMSCIMClient(settings(), sram(status=401)).get_directory()

    async def test_server_error(self):
        """A 5xx from SRAM is reported as a SRAM failure."""
        with pytest.raises(SRAMAPIError):
            await SRAMSCIMClient(settings(), sram(status=503)).get_directory()


def page(user: User | None, token: str | None = "scim-token", status: int = 200) -> httpx.Response:
    """Request /scim as the given user against a fake SRAM."""
    s = settings(token)
    app = create_demo_app(s)
    app.dependency_overrides[get_optional_user] = lambda: user
    app.dependency_overrides[get_scim_client] = lambda: SRAMSCIMClient(s, sram(status=status))
    return TestClient(app, follow_redirects=False).get("/scim")


def user(*entitlements: str) -> User:
    """A logged-in user with the given entitlements."""
    return User.from_claims(
        {"sub": "s", "voperson_id": "v@sram.surf.nl", "eduperson_entitlement": list(entitlements)}
    )


class TestSCIMPage:
    """The /scim page."""

    def test_requires_login(self):
        """Anonymous users are sent to login."""
        assert page(None).status_code == 303

    def test_requires_manager(self):
        """The page lists every member, so only managers may open it."""
        assert page(user("urn:mace:surf.nl:sram:group:uniharderwijk:other")).status_code == 403

    def test_manager_sees_directory(self):
        """Users and groups with their members are listed."""
        response = page(user(MANAGER))
        assert response.status_code == 200
        for text in (
            "jdoe",
            "Jane Doe",
            "jdoe@example.org",
            "Cumulus group",
            "uniharderwijk:cumulusgrp",
        ):
            assert text in response.text

    def test_not_configured(self):
        """Without a token the page explains that SCIM pull is off."""
        response = page(user(MANAGER), token=None)
        assert response.status_code == 200
        assert "SRAM_SCIM_TOKEN" in response.text

    def test_rejected_token(self):
        """A rejected token is shown as an error page."""
        response = page(user(MANAGER), status=401)
        assert response.status_code == 502
        assert "SCIM" in response.text

    def test_navigation_links_page(self):
        """Managers reach the page from the navigation."""
        assert 'href="/scim"' in page(user(MANAGER)).text


class TestNavigationLink:
    """The SCIM link appears in the navigation on every page, for managers only."""

    def home(self, entitlements: list[str]) -> str:
        """Render the home page for a logged-in user."""
        s = settings()
        app = create_demo_app(s)
        app.dependency_overrides[get_optional_user] = lambda: user(*entitlements)
        return TestClient(app).get("/").text

    def test_manager_sees_link_on_home_page(self):
        """Managers reach /scim from the home page."""
        assert 'href="/scim"' in self.home([MANAGER])

    def test_member_does_not_see_link(self):
        """Other users do not get a link to a page they cannot open."""
        assert 'href="/scim"' not in self.home(["urn:mace:surf.nl:sram:group:uniharderwijk:other"])


class TestSCIMGroupButtons:
    """The SCIM page offers an access test for every group SRAM returns."""

    def test_button_per_group(self):
        """Each group with a URN gets a Test button for its entitlement."""
        html = page(user(MANAGER)).text
        assert 'data-test-url="/demo/groups/uniharderwijk:cumulusgrp/editors"' in html
        assert html.count('data-test-url="/demo/groups/') == 1


class TestPaging:
    """SRAM may split a ListResponse over several pages."""

    async def test_reads_all_pages(self):
        """Users on later pages are fetched by startIndex until totalResults is reached."""
        users = [{"id": f"u-{i}", "userName": f"user{i}"} for i in range(1, 4)]
        starts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/Groups"):
                return httpx.Response(200, json={"totalResults": 0, "Resources": []})
            start = int(request.url.params.get("startIndex", "1"))
            starts.append(str(start))
            page_items = users[start - 1 : start + 1]
            return httpx.Response(
                200,
                json={
                    "totalResults": len(users),
                    "startIndex": start,
                    "itemsPerPage": len(page_items),
                    "Resources": page_items,
                },
            )

        directory = await SRAMSCIMClient(settings(), httpx.MockTransport(handler)).get_directory()
        assert [u.user_name for u in directory.users] == ["user1", "user2", "user3"]
        assert starts == ["1", "3"]
