"""Tests for the collaboration management pages of the demo application."""

import pytest
from fastapi.testclient import TestClient

from sram_fastapi.auth import User, get_optional_user
from sram_fastapi.collaborations import (
    Collaboration,
    Group,
    Membership,
    Organisation,
    OrganisationTokenError,
    Service,
    get_organisation_client,
)
from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app

MANAGER_ENTITLEMENT = "urn:mace:surf.nl:sram:group:uniharderwijk:managers"
CO_IDENTIFIER = "301ee8e6-b5d1-40b5-a27e-47611f803371"
OTHER_IDENTIFIER = "9f1c0000-0000-0000-0000-000000000002"


def collaboration() -> Collaboration:
    """Build a collaboration with two members, a group and a connected service."""
    return Collaboration(
        identifier=CO_IDENTIFIER,
        name="Cumulus research group",
        short_name="cumulusgrp",
        description="Cumulus research group.",
        global_urn="uniharderwijk:cumulusgrp",
        status="active",
        disclose_member_information=True,
        disclose_email_information=False,
        memberships=[
            Membership(
                uid="admin-uid@sram.eduteams.org",
                role="admin",
                status="active",
                name="Admin Doe",
                email="adoe@uniharderwijk.nl",
            ),
            Membership(
                uid="member-uid@sram.eduteams.org",
                role="member",
                status="active",
                name="Researcher Doe",
                email="rdoe@uniharderwijk.nl",
            ),
        ],
        groups=[Group(identifier="group-1", name="AI researchers", short_name="ai_researchers")],
        services=[Service(entity_id="https://service.cloud.example.com", name="Cloud service")],
    )


def other_collaboration() -> Collaboration:
    """Build a collaboration the test user does not belong to."""
    return Collaboration(
        identifier=OTHER_IDENTIFIER,
        name="Nimbus research group",
        short_name="nimbusgrp",
        global_urn="uniharderwijk:nimbusgrp",
    )


class FakeClient:
    """Stub organisation client that serves fixed data."""

    def __init__(self, configured: bool = True, error: Exception | None = None):
        self.configured = configured
        self.error = error

    async def get_organisation(self) -> Organisation:
        """Return the organisation with both collaborations."""
        if self.error:
            raise self.error
        return Organisation(
            identifier="org-1",
            name="University of Harderwijk",
            short_name="uniharderwijk",
            collaborations=[collaboration(), other_collaboration()],
        )

    async def get_collaboration(self, identifier: str) -> Collaboration:
        """Return the collaboration by identifier."""
        if self.error:
            raise self.error
        return collaboration() if identifier == CO_IDENTIFIER else other_collaboration()


def user_with(*entitlements: str, sub: str = "member-uid@sram.eduteams.org") -> User:
    """Build an authenticated user with the given entitlements."""
    return User.from_claims(
        {
            "sub": sub,
            "email": "rdoe@uniharderwijk.nl",
            "name": "Researcher Doe",
            "eduperson_entitlement": list(entitlements),
        }
    )


MEMBER = "urn:mace:surf.nl:sram:group:uniharderwijk:cumulusgrp"


@pytest.fixture
def settings() -> Settings:
    """Settings with collaboration management configured."""
    return Settings(
        secret_key="test-secret-key",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_organisation_api_token="test-organisation-token",
        sram_service_entity_id="https://service.cloud.example.com",
        collaboration_manager_entitlement=MANAGER_ENTITLEMENT,
        base_url="http://testserver",
    )


def build_client(settings: Settings, user: User | None, client: FakeClient) -> TestClient:
    """Create a test client with the session user and organisation client replaced."""
    app = create_demo_app(settings)
    app.dependency_overrides[get_optional_user] = lambda: user
    app.dependency_overrides[get_organisation_client] = lambda: client
    return TestClient(app)


class TestCollaborationList:
    """Tests for the collaboration overview page."""

    def test_requires_login(self, settings: Settings):
        """An anonymous visitor is sent to the login flow."""
        http = build_client(settings, None, FakeClient())
        response = http.get("/collaborations", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/auth/login"

    def test_lists_only_own_collaborations(self, settings: Settings):
        """A member sees the collaborations their entitlements imply, and no others."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        response = http.get("/collaborations")
        assert response.status_code == 200
        assert "Cumulus research group" in response.text
        assert "Nimbus research group" not in response.text

    def test_manager_sees_whole_organisation(self, settings: Settings):
        """A manager additionally sees every collaboration of the organisation."""
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), FakeClient())
        response = http.get("/collaborations")
        assert response.status_code == 200
        assert "University of Harderwijk" in response.text
        assert "Nimbus research group" in response.text

    def test_reports_missing_configuration(self, settings: Settings):
        """Without an organisation token the page explains what is missing."""
        http = build_client(settings, user_with(MEMBER), FakeClient(configured=False))
        response = http.get("/collaborations")
        assert response.status_code == 200
        assert "SRAM_ORGANISATION_API_TOKEN" in response.text

    def test_reports_sram_failure(self, settings: Settings):
        """A rejected organisation token is reported as an upstream failure."""
        http = build_client(
            settings, user_with(MEMBER), FakeClient(error=OrganisationTokenError("bad token"))
        )
        response = http.get("/collaborations")
        assert response.status_code == 502
        assert "SRAM could not be reached" in response.text
        assert "bad token" in response.text


class TestCollaborationDetail:
    """Tests for the collaboration detail page."""

    def test_requires_login(self, settings: Settings):
        """An anonymous visitor is sent to the login flow."""
        http = build_client(settings, None, FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}", follow_redirects=False)
        assert response.status_code == 307

    def test_non_member_is_refused(self, settings: Settings):
        """A user without the collaboration entitlement cannot open it."""
        http = build_client(
            settings, user_with("urn:mace:surf.nl:sram:group:other:co"), FakeClient()
        )
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert response.status_code == 403

    def test_member_sees_members(self, settings: Settings):
        """A member sees the membership list with roles."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert response.status_code == 200
        assert "Admin Doe" in response.text
        assert "admin" in response.text

    def test_member_does_not_see_undisclosed_emails(self, settings: Settings):
        """Email addresses stay hidden while disclose_email_information is false."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "adoe@uniharderwijk.nl" not in response.text

    def test_admin_sees_emails(self, settings: Settings):
        """An admin of the collaboration sees the full membership details."""
        user = user_with(MEMBER, sub="admin-uid@sram.eduteams.org")
        http = build_client(settings, user, FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "adoe@uniharderwijk.nl" in response.text

    def test_manager_may_open_any_collaboration(self, settings: Settings):
        """A manager can open a collaboration they are not a member of."""
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert response.status_code == 200

    def test_shows_groups_and_services(self, settings: Settings):
        """Groups and connected services are listed."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "AI researchers" in response.text
        assert "https://service.cloud.example.com" in response.text
