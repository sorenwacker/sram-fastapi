"""Tests for the SCIM push endpoints SRAM provisions into."""

import pytest
from fastapi.testclient import TestClient

from sram_fastapi.auth import User, get_optional_user
from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app
from sram_fastapi.scim_push import SCIMStore, get_scim_store

TOKEN = "push-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MANAGER = "urn:mace:surf.nl:sram:group:uniharderwijk:managers"

USER = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "externalId": "abc123@sram.surf.nl",
    "userName": "jdoe",
    "displayName": "Jane Doe",
    "emails": [{"value": "jdoe@example.org", "primary": True}],
}
GROUP = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
    "externalId": "grp-1@sram.surf.nl",
    "displayName": "Cumulus group",
    "members": [],
}


def make_client(token: str | None = TOKEN, user: User | None = None) -> TestClient:
    """An app with SCIM push configured and a fresh store."""
    settings = Settings(
        _env_file=None,
        secret_key="test-secret-key",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_scim_push_token=token,
        collaboration_manager_entitlement=MANAGER,
        base_url="http://testserver",
    )
    app = create_demo_app(settings)
    store = SCIMStore()
    app.dependency_overrides[get_scim_store] = lambda: store
    app.dependency_overrides[get_optional_user] = lambda: user
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client() -> TestClient:
    """Client with push enabled."""
    return make_client()


class TestAuthentication:
    """Push requests must carry the configured token."""

    def test_missing_token_is_rejected(self, client):
        """No Authorization header gives 401."""
        assert client.get("/scim/v2/Users").status_code == 401

    def test_wrong_token_is_rejected(self, client):
        """A wrong bearer token gives 401 in SCIM error format."""
        response = client.get("/scim/v2/Users", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401
        assert "urn:ietf:params:scim:api:messages:2.0:Error" in response.json()["schemas"]

    def test_off_when_unconfigured(self):
        """Without SRAM_SCIM_PUSH_TOKEN the endpoints do not exist."""
        assert make_client(token=None).get("/scim/v2/Users", headers=AUTH).status_code == 404


@pytest.mark.parametrize(("kind", "body"), [("Users", USER), ("Groups", GROUP)])
class TestLifecycle:
    """Create, read, find, replace and delete for users and groups."""

    def test_create_assigns_id(self, client, kind, body):
        """POST stores the resource and returns it with an id and 201."""
        response = client.post(f"/scim/v2/{kind}", json=body, headers=AUTH)
        assert response.status_code == 201
        created = response.json()
        assert created["id"]
        assert created["externalId"] == body["externalId"]
        assert created["meta"]["resourceType"] == kind[:-1]
        got = client.get(f"/scim/v2/{kind}/{created['id']}", headers=AUTH)
        assert got.json()["externalId"] == body["externalId"]

    def test_filter_by_external_id(self, client, kind, body):
        """SRAM finds existing resources by externalId."""
        client.post(f"/scim/v2/{kind}", json=body, headers=AUTH)
        hit = client.get(
            f"/scim/v2/{kind}",
            params={"filter": f'externalId eq "{body["externalId"]}"'},
            headers=AUTH,
        ).json()
        miss = client.get(
            f"/scim/v2/{kind}", params={"filter": 'externalId eq "other"'}, headers=AUTH
        ).json()
        assert hit["totalResults"] == 1
        assert miss["totalResults"] == 0
        assert miss["Resources"] == []

    def test_replace(self, client, kind, body):
        """PUT replaces the stored resource and keeps its id."""
        rid = client.post(f"/scim/v2/{kind}", json=body, headers=AUTH).json()["id"]
        changed = {**body, "displayName": "Renamed"}
        response = client.put(f"/scim/v2/{kind}/{rid}", json=changed, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["id"] == rid
        assert client.get(f"/scim/v2/{kind}/{rid}", headers=AUTH).json()["displayName"] == "Renamed"

    def test_delete(self, client, kind, body):
        """DELETE removes the resource."""
        rid = client.post(f"/scim/v2/{kind}", json=body, headers=AUTH).json()["id"]
        assert client.delete(f"/scim/v2/{kind}/{rid}", headers=AUTH).status_code == 204
        assert client.get(f"/scim/v2/{kind}/{rid}", headers=AUTH).status_code == 404

    def test_unknown_id(self, client, kind, body):
        """PUT and DELETE on an unknown id give 404."""
        assert client.put(f"/scim/v2/{kind}/missing", json=body, headers=AUTH).status_code == 404
        assert client.delete(f"/scim/v2/{kind}/missing", headers=AUTH).status_code == 404


class TestPage:
    """Pushed data is visible on the /scim page."""

    def test_page_shows_pushed_resources(self):
        """Managers see pushed users and groups."""
        manager = User.from_claims(
            {"sub": "s", "voperson_id": "v@sram.surf.nl", "eduperson_entitlement": [MANAGER]}
        )
        client = make_client(user=manager)
        client.post("/scim/v2/Users", json=USER, headers=AUTH)
        client.post("/scim/v2/Groups", json=GROUP, headers=AUTH)
        text = client.get("/scim").text
        assert "Received via push" in text
        assert "jdoe" in text
        assert "Cumulus group" in text
