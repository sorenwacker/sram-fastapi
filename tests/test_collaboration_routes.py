"""Tests for the collaboration management pages of the demo application."""

import re

import pytest
from fastapi.testclient import TestClient

from sram_fastapi.auth import User, get_optional_user
from sram_fastapi.collaborations import (
    Collaboration,
    CollaborationCreate,
    CollaborationNotFoundError,
    Group,
    Invitation,
    Membership,
    Organisation,
    OrganisationTokenError,
    Service,
    SRAMAPIError,
    get_organisation_client,
)
from sram_fastapi.config import Settings
from sram_fastapi.demo.app import create_demo_app

MANAGER_ENTITLEMENT = "urn:mace:surf.nl:sram:group:uniharderwijk:managers"
CO_IDENTIFIER = "301ee8e6-b5d1-40b5-a27e-47611f803371"
OTHER_IDENTIFIER = "9f1c0000-0000-0000-0000-000000000002"


PAYLOAD_NAME = "'); alert(1); //"


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
                name=PAYLOAD_NAME,
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
    """Stub organisation client that serves fixed data and records calls."""

    def __init__(self, configured: bool = True, error: Exception | None = None):
        self.configured = configured
        self.error = error
        self.created: CollaborationCreate | None = None
        self.connected: list[str] = []
        self.deleted: list[str] = []
        self.invited: list[dict] = []
        self.roles: list[tuple[str, str, str]] = []
        self.removed: list[tuple[str, str]] = []
        self.invitation_actions: list[tuple[str, str]] = []
        self.groups_created: list[dict] = []
        self.groups_updated: list[dict] = []
        self.groups_deleted: list[str] = []
        self.group_members: list[tuple[str, str, str]] = []
        self.service_actions: list[tuple[str, str]] = []
        self.member_error: Exception | None = None
        self.invitation_list_error: Exception | None = None
        self.connect_error: Exception | None = None

    async def disconnect_service(self, identifier: str) -> None:
        """Record a service disconnection."""
        self.service_actions.append(("disconnect", identifier))

    async def create_group(self, identifier, name, short_name, description=None, **kwargs):
        """Record a group creation."""
        self.groups_created.append(
            {
                "identifier": identifier,
                "name": name,
                "short_name": short_name,
                "auto_provision_members": kwargs.get("auto_provision_members", False),
            }
        )
        return Group(identifier="group-1", name=name, short_name=short_name)

    async def update_group(self, group_identifier, name=None, description=None, **kwargs):
        """Record a group update."""
        self.groups_updated.append({"group": group_identifier, "name": name})
        return Group(identifier=group_identifier, name=name or "")

    async def delete_group(self, group_identifier: str) -> None:
        """Record a group deletion."""
        self.groups_deleted.append(group_identifier)

    async def add_group_member(self, group_identifier: str, uid: str) -> None:
        """Record a group membership addition."""
        self.group_members.append(("add", group_identifier, uid))

    async def remove_group_member(self, group_identifier: str, uid: str) -> None:
        """Record a group membership removal."""
        self.group_members.append(("remove", group_identifier, uid))

    async def list_open_invitations(self, identifier: str) -> list[Invitation]:
        """Return one open invitation, or fail when the test asked for a failure."""
        if self.invitation_list_error:
            raise self.invitation_list_error
        if self.error:
            raise self.error
        return [
            Invitation(
                identifier="inv-1",
                email="pending@uniharderwijk.nl",
                intended_role="member",
                status="open",
            )
        ]

    async def invite(self, identifier: str, emails, role, message=None, **kwargs):
        """Record an invitation."""
        self.invited.append({"identifier": identifier, "emails": emails, "role": role})
        return []

    async def set_member_role(self, identifier: str, uid: str, role: str) -> None:
        """Record a role change."""
        self.roles.append((identifier, uid, role))

    async def remove_member(self, identifier: str, uid: str) -> None:
        """Record a removal, or fail when the test asked for a failure."""
        if self.member_error:
            raise self.member_error
        self.removed.append((identifier, uid))

    async def resend_invitation(self, external_identifier: str) -> None:
        """Record a resend."""
        self.invitation_actions.append(("resend", external_identifier))

    async def update_invitation(self, external_identifier: str, role=None, groups=None) -> None:
        """Record an invitation update."""
        self.invitation_actions.append((f"role:{role}", external_identifier))

    async def withdraw_invitation(self, external_identifier: str) -> None:
        """Record a withdrawal."""
        self.invitation_actions.append(("withdraw", external_identifier))

    async def create_collaboration(self, spec: "CollaborationCreate") -> Collaboration:
        """Record the creation and return the new collaboration."""
        if self.error:
            raise self.error
        self.created = spec
        return collaboration()

    async def connect_service(self, identifier: str) -> None:
        """Record the service connection, or fail when the test asked for a failure."""
        if self.connect_error:
            raise self.connect_error
        self.connected.append(identifier)
        self.service_actions.append(("connect", identifier))

    async def delete_collaboration(self, identifier: str) -> None:
        """Record the deletion."""
        self.deleted.append(identifier)

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
        collaboration_deletion_enabled=True,
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
        assert response.status_code == 303
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
        assert response.status_code == 303

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


class TestProvisioning:
    """Tests for creating and deleting collaborations from the demo application."""

    def test_form_requires_manager_entitlement(self, settings: Settings):
        """A member without the manager entitlement cannot open the creation form."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        assert http.get("/collaborations/new").status_code == 403

    def test_manager_sees_form(self, settings: Settings):
        """A manager sees the creation form."""
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), FakeClient())
        response = http.get("/collaborations/new")
        assert response.status_code == 200
        assert "New collaboration" in response.text

    def test_form_reports_missing_service_entity_id(self, settings: Settings):
        """Without a service entity ID the form explains why creation is unavailable."""
        settings.sram_service_entity_id = None
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), FakeClient())
        response = http.get("/collaborations/new")
        assert response.status_code == 200
        assert "SRAM_SERVICE_ENTITY_ID" in response.text

    def test_create_connects_service_and_redirects(self, settings: Settings):
        """Creating a collaboration also connects this service, then opens the detail page."""
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)

        response = http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl, adoe@uniharderwijk.nl",
                "short_name": "cumulusgrp",
                "disclose_member_information": "on",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == f"/collaborations/{CO_IDENTIFIER}"
        assert fake.created.name == "Cumulus research group"
        assert fake.created.administrators == [
            "jdoe@uniharderwijk.nl",
            "adoe@uniharderwijk.nl",
        ]
        assert fake.created.disclose_member_information is True
        assert fake.created.disclose_email_information is False
        assert fake.connected == [CO_IDENTIFIER]

    def test_create_accepts_an_expiry_date(self, settings: Settings):
        """An expiry date on the form reaches SRAM as epoch seconds."""
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)

        http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
                "expiry_date": "2027-01-01",
            },
            follow_redirects=False,
        )

        assert fake.created.expiry_date == 1798761600

    def test_create_without_expiry_date(self, settings: Settings):
        """An empty expiry date leaves the collaboration without one."""
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)

        http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
                "expiry_date": "",
            },
            follow_redirects=False,
        )

        assert fake.created.expiry_date is None

    def test_create_rejects_a_malformed_expiry_date(self, settings: Settings):
        """A date SRAM could not use is refused before the request is sent."""
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)

        response = http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
                "expiry_date": "not-a-date",
            },
        )

        assert response.status_code == 400
        assert fake.created is None

    def test_create_requires_manager_entitlement(self, settings: Settings):
        """A member without the manager entitlement cannot create a collaboration."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER), fake)
        response = http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
            },
        )
        assert response.status_code == 403
        assert fake.created is None

    def test_create_refused_without_service_entity_id(self, settings: Settings):
        """Creation is refused while the service entity ID is missing."""
        settings.sram_service_entity_id = None
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)
        response = http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
            },
        )
        assert response.status_code == 400
        assert fake.created is None

    def test_delete_requires_manager_entitlement(self, settings: Settings):
        """A collaboration admin without the manager entitlement cannot delete it."""
        fake = FakeClient()
        user = user_with(MEMBER, sub="admin-uid@sram.eduteams.org")
        http = build_client(settings, user, fake)
        response = http.post(f"/collaborations/{CO_IDENTIFIER}/delete")
        assert response.status_code == 403
        assert fake.deleted == []

    def test_manager_deletes_collaboration(self, settings: Settings):
        """A manager deletes the collaboration and returns to the overview."""
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)
        response = http.post(f"/collaborations/{CO_IDENTIFIER}/delete", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/collaborations"
        assert fake.deleted == [CO_IDENTIFIER]


ADMIN = "admin-uid@sram.eduteams.org"


class TestMembershipManagement:
    """Tests for managing members, admins and invitations from the demo application."""

    def test_member_cannot_invite(self, settings: Settings):
        """An ordinary member cannot invite users."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/invite",
            data={"emails": "new@uniharderwijk.nl", "role": "member"},
        )
        assert response.status_code == 403
        assert fake.invited == []

    def test_collaboration_admin_invites(self, settings: Settings):
        """An admin of the collaboration invites a user as member."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/invite",
            data={"emails": "new@uniharderwijk.nl, other@uniharderwijk.nl", "role": "admin"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/collaborations/{CO_IDENTIFIER}"
        assert fake.invited == [
            {
                "identifier": CO_IDENTIFIER,
                "emails": ["new@uniharderwijk.nl", "other@uniharderwijk.nl"],
                "role": "admin",
            }
        ]

    def test_manager_invites_without_being_member(self, settings: Settings):
        """A manager can invite into a collaboration they do not belong to."""
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/invite",
            data={"emails": "new@uniharderwijk.nl", "role": "member"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert fake.invited[0]["role"] == "member"

    def test_admin_promotes_member(self, settings: Settings):
        """An admin promotes a member to admin."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/members/role",
            data={"uid": "member-uid@sram.eduteams.org", "role": "admin"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert fake.roles == [(CO_IDENTIFIER, "member-uid@sram.eduteams.org", "admin")]

    def test_member_cannot_change_roles(self, settings: Settings):
        """An ordinary member cannot change roles."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/members/role",
            data={"uid": "member-uid@sram.eduteams.org", "role": "admin"},
        )
        assert response.status_code == 403
        assert fake.roles == []

    def test_rejects_unknown_role(self, settings: Settings):
        """A role other than admin or member is refused."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/members/role",
            data={"uid": "member-uid@sram.eduteams.org", "role": "owner"},
        )
        assert response.status_code == 422
        assert fake.roles == []

    def test_admin_removes_member(self, settings: Settings):
        """An admin removes a member."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/members/remove",
            data={"uid": "member-uid@sram.eduteams.org"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert fake.removed == [(CO_IDENTIFIER, "member-uid@sram.eduteams.org")]

    def test_admin_manages_invitations(self, settings: Settings):
        """An admin resends, re-roles and withdraws an invitation."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        base = f"/collaborations/{CO_IDENTIFIER}/invitations/inv-1"

        assert http.post(f"{base}/resend", follow_redirects=False).status_code == 303
        assert (
            http.post(f"{base}/role", data={"role": "admin"}, follow_redirects=False).status_code
            == 303
        )
        assert http.post(f"{base}/withdraw", follow_redirects=False).status_code == 303

        assert fake.invitation_actions == [
            ("resend", "inv-1"),
            ("role:admin", "inv-1"),
            ("withdraw", "inv-1"),
        ]

    def test_member_does_not_see_invitations(self, settings: Settings):
        """Pending invitations are shown to admins only."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "pending@uniharderwijk.nl" not in response.text

    def test_admin_sees_invitations_and_controls(self, settings: Settings):
        """An admin sees pending invitations and the management controls."""
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "pending@uniharderwijk.nl" in response.text
        assert f"/collaborations/{CO_IDENTIFIER}/invite" in response.text


class TestGroupManagement:
    """Tests for managing groups from the demo application."""

    def test_member_cannot_create_group(self, settings: Settings):
        """An ordinary member cannot create a group."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups",
            data={"name": "AI researchers", "short_name": "ai_researchers"},
        )
        assert response.status_code == 403
        assert fake.groups_created == []

    def test_admin_creates_group(self, settings: Settings):
        """An admin creates a group in the collaboration."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups",
            data={
                "name": "AI researchers",
                "short_name": "ai_researchers",
                "description": "AI researchers group",
                "auto_provision_members": "on",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert fake.groups_created == [
            {
                "identifier": CO_IDENTIFIER,
                "name": "AI researchers",
                "short_name": "ai_researchers",
                "auto_provision_members": True,
            }
        ]

    def test_admin_renames_group(self, settings: Settings):
        """An admin renames a group."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups/group-1/update",
            data={"name": "Renamed group"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert fake.groups_updated == [{"group": "group-1", "name": "Renamed group"}]

    def test_admin_deletes_group(self, settings: Settings):
        """An admin deletes a group."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups/group-1/delete", follow_redirects=False
        )
        assert response.status_code == 303
        assert fake.groups_deleted == ["group-1"]

    def test_admin_changes_group_membership(self, settings: Settings):
        """An admin adds and removes a group member."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        uid = "member-uid@sram.eduteams.org"

        assert (
            http.post(
                f"/collaborations/{CO_IDENTIFIER}/groups/group-1/members",
                data={"uid": uid},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            http.post(
                f"/collaborations/{CO_IDENTIFIER}/groups/group-1/members/remove",
                data={"uid": uid},
                follow_redirects=False,
            ).status_code
            == 303
        )

        assert fake.group_members == [("add", "group-1", uid), ("remove", "group-1", uid)]

    def test_member_does_not_see_group_controls(self, settings: Settings):
        """Group management controls are shown to admins only."""
        http = build_client(settings, user_with(MEMBER), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert f"/collaborations/{CO_IDENTIFIER}/groups/group-1/delete" not in response.text

    def test_admin_sees_group_controls(self, settings: Settings):
        """An admin sees the group management controls."""
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert f"/collaborations/{CO_IDENTIFIER}/groups/group-1/delete" in response.text


class TestServiceConnection:
    """Tests for connecting this service to a collaboration from the demo application."""

    def test_member_cannot_change_service_connection(self, settings: Settings):
        """An ordinary member cannot connect or disconnect the service."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER), fake)
        assert http.post(f"/collaborations/{CO_IDENTIFIER}/services/connect").status_code == 403
        assert http.post(f"/collaborations/{CO_IDENTIFIER}/services/disconnect").status_code == 403
        assert fake.service_actions == []

    def test_admin_connects_and_disconnects(self, settings: Settings):
        """An admin connects and disconnects this service."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        assert (
            http.post(
                f"/collaborations/{CO_IDENTIFIER}/services/connect", follow_redirects=False
            ).status_code
            == 303
        )
        assert (
            http.post(
                f"/collaborations/{CO_IDENTIFIER}/services/disconnect", follow_redirects=False
            ).status_code
            == 303
        )
        assert fake.service_actions == [
            ("connect", CO_IDENTIFIER),
            ("disconnect", CO_IDENTIFIER),
        ]


class TestFailureHandling:
    """Tests for anonymous state changes and SRAM failures during management actions."""

    MUTATIONS = [
        ("/invite", {"emails": "a@b.nl", "role": "member"}),
        ("/members/role", {"uid": "u", "role": "admin"}),
        ("/members/remove", {"uid": "u"}),
        ("/delete", {}),
        ("/groups", {"name": "g", "short_name": "g"}),
        ("/services/connect", {}),
    ]

    @pytest.mark.parametrize("path,data", MUTATIONS)
    def test_anonymous_post_lands_on_login(self, settings: Settings, path: str, data: dict):
        """An anonymous state change redirects to login as a GET, not as a repeated POST."""
        http = build_client(settings, None, FakeClient())
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}{path}", data=data, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"

        followed = http.post(
            f"/collaborations/{CO_IDENTIFIER}{path}", data=data, follow_redirects=True
        )
        assert followed.status_code != 405

    def test_sram_failure_during_mutation_is_reported(self, settings: Settings):
        """A SRAM failure while removing a member is reported, not raised as a server error."""
        fake = FakeClient()
        fake.member_error = OrganisationTokenError("token rejected")
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(f"/collaborations/{CO_IDENTIFIER}/members/remove", data={"uid": "u"})
        assert response.status_code == 502
        assert "token rejected" in response.text

    def test_missing_collaboration_is_not_found(self, settings: Settings):
        """An unknown collaboration is reported as not found, not as an upstream failure."""
        fake = FakeClient(error=CollaborationNotFoundError("no such collaboration"))
        http = build_client(settings, user_with(MEMBER), fake)
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert response.status_code == 404

    def test_invitation_failure_does_not_break_the_page(self, settings: Settings):
        """A failure listing invitations is reported instead of raising a server error."""
        fake = FakeClient()
        fake.invitation_list_error = SRAMAPIError("invitations unavailable")
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert response.status_code == 502
        assert "invitations unavailable" in response.text

    def test_failed_connect_rolls_back_the_collaboration(self, settings: Settings):
        """A collaboration that cannot be connected to this service is removed again."""
        fake = FakeClient()
        fake.connect_error = SRAMAPIError("connect failed")
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)
        response = http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
            },
        )
        assert response.status_code == 502
        assert fake.deleted == [CO_IDENTIFIER]


class TestObjectOwnership:
    """Tests that group and invitation actions stay inside the authorized collaboration."""

    def test_group_of_another_collaboration_is_refused(self, settings: Settings):
        """A group that does not belong to this collaboration cannot be deleted."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(f"/collaborations/{CO_IDENTIFIER}/groups/foreign-group/delete")
        assert response.status_code == 404
        assert fake.groups_deleted == []

    def test_foreign_group_membership_change_is_refused(self, settings: Settings):
        """A member cannot be added to a group of another collaboration."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups/foreign-group/members",
            data={"uid": "member-uid@sram.eduteams.org"},
        )
        assert response.status_code == 404
        assert fake.group_members == []

    def test_foreign_group_update_is_refused(self, settings: Settings):
        """A group of another collaboration cannot be renamed."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups/foreign-group/update",
            data={"name": "Renamed"},
        )
        assert response.status_code == 404
        assert fake.groups_updated == []

    def test_own_group_is_still_accepted(self, settings: Settings):
        """A group of this collaboration is still managed normally."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/groups/group-1/delete", follow_redirects=False
        )
        assert response.status_code == 303
        assert fake.groups_deleted == ["group-1"]

    def test_foreign_invitation_is_refused(self, settings: Settings):
        """An invitation that is not open in this collaboration cannot be withdrawn."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/invitations/foreign-invitation/withdraw"
        )
        assert response.status_code == 404
        assert fake.invitation_actions == []

    def test_own_invitation_is_still_accepted(self, settings: Settings):
        """An invitation of this collaboration is still managed normally."""
        fake = FakeClient()
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), fake)
        response = http.post(
            f"/collaborations/{CO_IDENTIFIER}/invitations/inv-1/resend", follow_redirects=False
        )
        assert response.status_code == 303
        assert fake.invitation_actions == [("resend", "inv-1")]


class TestConfirmationMarkup:
    """Tests that confirmation prompts cannot become an injection point."""

    def test_no_inline_event_handlers(self, settings: Settings):
        """Confirmations are wired up from a script, not from inline handlers."""
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert re.search(r'\son[a-z]+="', response.text) is None
        assert "data-confirm=" in response.text

    def test_member_name_never_reaches_a_script_context(self, settings: Settings):
        """A member name that looks like code never lands in JavaScript."""
        http = build_client(settings, user_with(MEMBER, sub=ADMIN), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")

        assert PAYLOAD_NAME not in response.text
        scripts = re.findall(r"<script>(.*?)</script>", response.text, re.DOTALL)
        assert scripts
        assert all("alert(1)" not in script for script in scripts)


class TestDeletionSwitch:
    """Tests for the deployment switch that governs collaboration deletion."""

    def test_deletion_is_off_by_default(self):
        """A deployment that says nothing about deletion does not allow it."""
        assert (
            Settings(
                secret_key="k",
                sram_oidc_client_id="id",
                sram_oidc_client_secret="secret",
            ).collaboration_deletion_enabled
            is False
        )

    def test_manager_cannot_delete_when_disabled(self, settings: Settings):
        """With deletion disabled the route refuses even a manager."""
        settings.collaboration_deletion_enabled = False
        fake = FakeClient()
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)
        response = http.post(f"/collaborations/{CO_IDENTIFIER}/delete")
        assert response.status_code == 403
        assert fake.deleted == []

    def test_page_explains_why_deletion_is_unavailable(self, settings: Settings):
        """The detail page names the setting rather than hiding the capability."""
        settings.collaboration_deletion_enabled = False
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "COLLABORATION_DELETION_ENABLED" in response.text
        assert f"/collaborations/{CO_IDENTIFIER}/delete" not in response.text

    def test_rollback_still_deletes_when_the_switch_is_off(self, settings: Settings):
        """A collaboration that could not be connected is still removed again."""
        settings.collaboration_deletion_enabled = False
        fake = FakeClient()
        fake.connect_error = SRAMAPIError("connect failed")
        http = build_client(settings, user_with(MANAGER_ENTITLEMENT), fake)

        response = http.post(
            "/collaborations/new",
            data={
                "name": "Cumulus research group",
                "description": "Cumulus research group.",
                "administrators": "jdoe@uniharderwijk.nl",
            },
        )

        assert response.status_code == 502
        assert fake.deleted == [CO_IDENTIFIER]


class TestAdminIdentityMatching:
    """Tests for matching the session user against a SRAM membership."""

    def test_same_hash_different_host_is_the_same_person(self, settings: Settings):
        """A proxy subject and an API uid that share their identifier match."""
        user = user_with(MEMBER, sub="admin-uid@sram.surf.nl")
        http = build_client(settings, user, FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert response.status_code == 200
        assert "adoe@uniharderwijk.nl" in response.text

    def test_different_identifier_is_not_an_admin(self, settings: Settings):
        """A different identifier does not become an admin through the host suffix."""
        user = user_with(MEMBER, sub="someone-else@sram.surf.nl")
        http = build_client(settings, user, FakeClient())
        response = http.get(f"/collaborations/{CO_IDENTIFIER}")
        assert "adoe@uniharderwijk.nl" not in response.text

    def test_identifier_without_a_host_still_matches(self, settings: Settings):
        """A uid that carries no host, such as urn:jdoe, is compared whole."""
        collaboration_admin = Membership(uid="urn:jdoe", role="admin")
        assert Collaboration(
            identifier="co", name="co", memberships=[collaboration_admin]
        ).admin_uids() == {"urn:jdoe"}
