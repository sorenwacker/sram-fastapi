"""Tests for the SRAM organisation API client."""

import json

import httpx
import pytest

from sram_fastapi.collaborations import (
    CollaborationConflictError,
    CollaborationCreate,
    CollaborationNotFoundError,
    OrganisationTokenError,
    SRAMAPIError,
    SRAMNotConfiguredError,
    SRAMOrganisationClient,
    entitlement_for,
)
from sram_fastapi.config import Settings

COLLABORATION_DETAIL = {
    "id": 999,
    "identifier": "301ee8e6-b5d1-40b5-a27e-47611f803371",
    "name": "Cumulus research group",
    "short_name": "cumulusgrp",
    "description": "Cumulus research group of the University of Harderwijk.",
    "global_urn": "uniharderwijk:cumulusgrp",
    "status": "active",
    "website_url": "https://research.uniharderwijk.nl/cumulusgrp",
    "disable_join_requests": True,
    "disclose_member_information": True,
    "disclose_email_information": False,
    "expiry_date": 1644015600,
    "collaboration_memberships_count": 2,
    "invitations_count": 1,
    "collaboration_memberships": [
        {
            "id": 1,
            "role": "admin",
            "status": "active",
            "expiry_date": None,
            "user": {
                "id": 11,
                "uid": "admin-uid@sram.eduteams.org",
                "name": "Admin Doe",
                "email": "adoe@uniharderwijk.nl",
            },
        },
        {
            "id": 2,
            "role": "member",
            "status": "active",
            "expiry_date": 1742000000,
            "user": {
                "id": 12,
                "uid": "member-uid@sram.eduteams.org",
                "name": "Researcher Doe",
                "email": "rdoe@uniharderwijk.nl",
            },
        },
    ],
    "groups": [
        {
            "id": 5,
            "identifier": "b1a1c2d3-0000-0000-0000-000000000001",
            "name": "AI researchers",
            "short_name": "ai_researchers",
            "description": "AI researchers group",
            "global_urn": "uniharderwijk:cumulusgrp:ai_researchers",
            "collaboration_memberships": [
                {"id": 2, "role": "member", "user": {"uid": "member-uid@sram.eduteams.org"}}
            ],
        }
    ],
    "services": [
        {
            "id": 888,
            "entity_id": "https://service.cloud.example.com",
            "name": "Cloud research service",
        }
    ],
}

ORGANISATION_DETAIL = {
    "id": 613,
    "identifier": "42de0064-cddc-4c36-9e19-c0fd6e782956",
    "name": "University of Harderwijk",
    "short_name": "uniharderwijk",
    "collaborations_count": 1,
    "collaborations": [
        {
            "id": 999,
            "identifier": "301ee8e6-b5d1-40b5-a27e-47611f803371",
            "name": "Cumulus research group",
            "short_name": "cumulusgrp",
            "description": "Cumulus research group.",
            "global_urn": "uniharderwijk:cumulusgrp",
        }
    ],
}


def make_settings(**overrides) -> Settings:
    """Build settings with the organisation API configured."""
    values = {
        "secret_key": "test-secret-key",
        "sram_oidc_client_id": "test-client-id",
        "sram_oidc_client_secret": "test-client-secret",
        "sram_api_base_url": "https://acc.sram.surf.nl",
        "sram_organisation_api_token": "test-organisation-token",
        "sram_service_entity_id": "https://service.cloud.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def make_client(handler, **overrides) -> SRAMOrganisationClient:
    """Build a client whose HTTP calls are served by handler."""
    return SRAMOrganisationClient(
        make_settings(**overrides),
        transport=httpx.MockTransport(handler),
    )


class TestConfiguration:
    """Tests for configuration handling."""

    def test_configured_when_token_present(self):
        """Client reports configured when the organisation token is set."""
        assert SRAMOrganisationClient(make_settings()).configured is True

    def test_not_configured_without_token(self):
        """Client reports unconfigured when the organisation token is absent."""
        settings = make_settings(sram_organisation_api_token=None)
        assert SRAMOrganisationClient(settings).configured is False

    async def test_call_without_token_raises(self):
        """Calling the API without a token raises rather than contacting SRAM."""
        client = SRAMOrganisationClient(make_settings(sram_organisation_api_token=None))
        with pytest.raises(SRAMNotConfiguredError):
            await client.get_organisation()


class TestRequests:
    """Tests for the requests sent to SRAM."""

    async def test_get_organisation_uses_base_url_and_token(self):
        """Organisation request targets the configured base URL with a bearer token."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=ORGANISATION_DETAIL)

        organisation = await make_client(handler).get_organisation()

        assert seen["url"] == "https://acc.sram.surf.nl/api/organisations/v1"
        assert seen["auth"] == "bearer test-organisation-token"
        assert organisation.name == "University of Harderwijk"
        assert organisation.short_name == "uniharderwijk"
        assert [c.short_name for c in organisation.collaborations] == ["cumulusgrp"]

    async def test_get_collaboration_parses_detail(self):
        """Collaboration detail is parsed into a Collaboration."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/collaborations/v1/301ee8e6-b5d1-40b5-a27e-47611f803371"
            return httpx.Response(200, json=COLLABORATION_DETAIL)

        collaboration = await make_client(handler).get_collaboration(
            "301ee8e6-b5d1-40b5-a27e-47611f803371"
        )

        assert collaboration.name == "Cumulus research group"
        assert collaboration.global_urn == "uniharderwijk:cumulusgrp"
        assert collaboration.disclose_member_information is True
        assert collaboration.disclose_email_information is False
        assert [g.name for g in collaboration.groups] == ["AI researchers"]
        assert [s.entity_id for s in collaboration.services] == [
            "https://service.cloud.example.com"
        ]

    async def test_list_members_flattens_nested_user(self):
        """Memberships expose the nested user's uid, name and email."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=COLLABORATION_DETAIL)

        members = await make_client(handler).list_members("301ee8e6-b5d1-40b5-a27e-47611f803371")

        assert [m.uid for m in members] == [
            "admin-uid@sram.eduteams.org",
            "member-uid@sram.eduteams.org",
        ]
        assert [m.role for m in members] == ["admin", "member"]
        assert members[0].name == "Admin Doe"
        assert members[1].email == "rdoe@uniharderwijk.nl"
        assert members[1].expiry_date == 1742000000

    async def test_membership_without_user_object(self):
        """A membership without a nested user does not break parsing."""
        payload = dict(
            COLLABORATION_DETAIL, collaboration_memberships=[{"id": 3, "role": "member"}]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        members = await make_client(handler).list_members("any-identifier")

        assert members[0].uid is None
        assert members[0].name is None


class TestErrorMapping:
    """Tests for mapping SRAM responses to exceptions."""

    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_token_errors(self, status_code: int):
        """401 and 403 signal a problem with the organisation token."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={"message": "no"})

        with pytest.raises(OrganisationTokenError):
            await make_client(handler).get_organisation()

    async def test_not_found(self):
        """404 signals an unknown collaboration."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "gone"})

        with pytest.raises(CollaborationNotFoundError):
            await make_client(handler).get_collaboration("missing")

    async def test_conflict(self):
        """409 signals a conflicting state."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"message": "exists"})

        with pytest.raises(CollaborationConflictError):
            await make_client(handler).get_collaboration("duplicate")

    async def test_transport_failure(self):
        """A transport failure is reported as an API error, not as invalid data."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(SRAMAPIError):
            await make_client(handler).get_organisation()

    async def test_unexpected_status(self):
        """An unexpected status is reported as an API error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(SRAMAPIError):
            await make_client(handler).get_organisation()


class TestEntitlementMapping:
    """Tests for mapping a collaboration to its entitlement."""

    def test_entitlement_for_global_urn(self):
        """A global URN maps to the SRAM group entitlement of the collaboration."""
        assert (
            entitlement_for("uniharderwijk:cumulusgrp")
            == "urn:mace:surf.nl:sram:group:uniharderwijk:cumulusgrp"
        )


class TestProvisioning:
    """Tests for creating, connecting and deleting collaborations."""

    async def test_create_collaboration_sends_required_fields(self):
        """Creation sends the fields SRAM requires, and returns the new collaboration."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json=COLLABORATION_DETAIL)

        created = await make_client(handler).create_collaboration(
            CollaborationCreate(
                name="Cumulus research group",
                description="Cumulus research group.",
                administrators=["jdoe@uniharderwijk.nl"],
                short_name="cumulusgrp",
                disable_join_requests=True,
                disclose_member_information=True,
                disclose_email_information=False,
                message="Please join.",
                tags=["label_test"],
                units=["fac_wiskunde"],
            )
        )

        assert seen["method"] == "POST"
        assert seen["path"] == "/api/collaborations/v1"
        assert seen["body"]["name"] == "Cumulus research group"
        assert seen["body"]["administrators"] == ["jdoe@uniharderwijk.nl"]
        assert seen["body"]["disable_join_requests"] is True
        assert seen["body"]["disclose_member_information"] is True
        assert seen["body"]["disclose_email_information"] is False
        assert seen["body"]["short_name"] == "cumulusgrp"
        assert seen["body"]["tags"] == ["label_test"]
        assert created.identifier == "301ee8e6-b5d1-40b5-a27e-47611f803371"

    async def test_create_collaboration_omits_empty_optionals(self):
        """Optional fields that were not given are left out of the request."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json=COLLABORATION_DETAIL)

        await make_client(handler).create_collaboration(
            CollaborationCreate(
                name="Cumulus research group",
                description="Cumulus research group.",
                administrators=["jdoe@uniharderwijk.nl"],
            )
        )

        assert "short_name" not in seen["body"]
        assert "website_url" not in seen["body"]
        assert "tags" not in seen["body"]

    async def test_connect_service_uses_configured_entity_id(self):
        """Connecting falls back to the service entity ID from settings."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"status": "connected"})

        await make_client(handler).connect_service("co-1")

        assert seen["method"] == "PUT"
        assert seen["path"] == (
            "/api/collaborations_services/v1/connect_collaboration_service/co-1"
        )
        assert seen["body"] == {"service_entity_id": "https://service.cloud.example.com"}

    async def test_connect_service_without_entity_id_raises(self):
        """Connecting without a configured entity ID raises rather than guessing."""
        client = SRAMOrganisationClient(make_settings(sram_service_entity_id=None))
        with pytest.raises(SRAMNotConfiguredError):
            await client.connect_service("co-1")

    async def test_delete_collaboration(self):
        """Deleting a collaboration sends DELETE and accepts an empty response."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(204)

        await make_client(handler).delete_collaboration("co-1")

        assert seen["method"] == "DELETE"
        assert seen["path"] == "/api/collaborations/v1/co-1"


OPEN_INVITATIONS = [
    {
        "status": "open",
        "intended_role": "member",
        "invitation": {
            "identifier": "E40BBF21-1606-4477-8167-674DCB8B62D6",
            "email": "rdoe@uniharderwijk.nl",
            "expiry_date": 1644015600,
        },
        "groups": [{"identifier": "group-1", "name": "AI researchers"}],
    }
]


class TestMembership:
    """Tests for invitations and membership changes."""

    async def test_invite_sends_bulk_invitation(self):
        """Inviting sends the collaboration, the emails and the intended role."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json=[
                    {
                        "email": "rdoe@uniharderwijk.nl",
                        "invitation_id": "E40BBF21-1606-4477-8167-674DCB8B62D6",
                        "status": "open",
                        "invitation_expiry_date": 1644015600,
                    }
                ],
            )

        invitations = await make_client(handler).invite(
            "co-1",
            emails=["rdoe@uniharderwijk.nl"],
            role="admin",
            message="Please join.",
            groups=["group-1"],
        )

        assert seen["method"] == "PUT"
        assert seen["path"] == "/api/invitations/v1/collaboration_invites"
        assert seen["body"]["collaboration_identifier"] == "co-1"
        assert seen["body"]["invites"] == ["rdoe@uniharderwijk.nl"]
        assert seen["body"]["intended_role"] == "admin"
        assert seen["body"]["message"] == "Please join."
        assert seen["body"]["groups"] == ["group-1"]
        assert invitations[0].identifier == "E40BBF21-1606-4477-8167-674DCB8B62D6"
        assert invitations[0].email == "rdoe@uniharderwijk.nl"

    async def test_list_open_invitations(self):
        """Open invitations are parsed from the nested invitation object."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/invitations/v1/invitations/co-1"
            return httpx.Response(200, json=OPEN_INVITATIONS)

        invitations = await make_client(handler).list_open_invitations("co-1")

        assert invitations[0].identifier == "E40BBF21-1606-4477-8167-674DCB8B62D6"
        assert invitations[0].email == "rdoe@uniharderwijk.nl"
        assert invitations[0].intended_role == "member"
        assert invitations[0].status == "open"
        assert invitations[0].expiry_date == 1644015600

    async def test_resend_invitation(self):
        """Resending an invitation targets the invitation's external identifier."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(201, json={})

        await make_client(handler).resend_invitation("inv-1")

        assert seen["method"] == "PUT"
        assert seen["path"] == "/api/invitations/v1/resend/inv-1"

    async def test_update_invitation_role(self):
        """Updating an invitation sends the new intended role."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={})

        await make_client(handler).update_invitation("inv-1", role="admin")

        assert seen["method"] == "PATCH"
        assert seen["path"] == "/api/invitations/v1/update/inv-1"
        assert seen["body"] == {"intended_role": "admin"}

    async def test_withdraw_invitation(self):
        """Withdrawing an invitation deletes it."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(204)

        await make_client(handler).withdraw_invitation("inv-1")

        assert seen["method"] == "DELETE"
        assert seen["path"] == "/api/invitations/v1/inv-1"

    async def test_set_member_role(self):
        """Changing a role sends the uid and the new role."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={})

        await make_client(handler).set_member_role("co-1", "member-uid@sram.eduteams.org", "admin")

        assert seen["method"] == "PUT"
        assert seen["path"] == "/api/collaborations/v1/co-1/members"
        assert seen["body"] == {"uid": "member-uid@sram.eduteams.org", "role": "admin"}

    async def test_remove_member(self):
        """Removing a member targets the membership by uid."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(204)

        await make_client(handler).remove_member("co-1", "member-uid@sram.eduteams.org")

        assert seen["method"] == "DELETE"
        assert seen["path"] == "/api/collaborations/v1/co-1/members/member-uid@sram.eduteams.org"


class TestGroups:
    """Tests for managing groups inside a collaboration."""

    async def test_create_group(self):
        """Creating a group sends the collaboration identifier and the group attributes."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "identifier": "group-1",
                    "name": "AI researchers",
                    "short_name": "ai_researchers",
                },
            )

        group = await make_client(handler).create_group(
            "co-1",
            name="AI researchers",
            short_name="ai_researchers",
            description="AI researchers group",
            auto_provision_members=True,
        )

        assert seen["method"] == "POST"
        assert seen["path"] == "/api/groups/v1"
        assert seen["body"] == {
            "collaboration_identifier": "co-1",
            "name": "AI researchers",
            "short_name": "ai_researchers",
            "auto_provision_members": True,
            "description": "AI researchers group",
        }
        assert group.identifier == "group-1"

    async def test_update_group(self):
        """Updating a group sends only the changed attributes."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"identifier": "group-1", "name": "Renamed"})

        await make_client(handler).update_group("group-1", name="Renamed")

        assert seen["method"] == "PUT"
        assert seen["path"] == "/api/groups/v1/group-1"
        assert seen["body"] == {"name": "Renamed"}

    async def test_delete_group(self):
        """Deleting a group targets the group identifier."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(204)

        await make_client(handler).delete_group("group-1")

        assert seen["method"] == "DELETE"
        assert seen["path"] == "/api/groups/v1/group-1"

    async def test_add_group_member(self):
        """Adding a group member sends the uid."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={})

        await make_client(handler).add_group_member("group-1", "member-uid@sram.eduteams.org")

        assert seen["method"] == "POST"
        assert seen["path"] == "/api/groups/v1/group-1"
        assert seen["body"] == {"uid": "member-uid@sram.eduteams.org"}

    async def test_remove_group_member(self):
        """Removing a group member targets the membership by uid."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(204)

        await make_client(handler).remove_group_member("group-1", "member-uid@sram.eduteams.org")

        assert seen["method"] == "DELETE"
        assert seen["path"] == "/api/groups/v1/group-1/members/member-uid@sram.eduteams.org"


class TestServiceConnection:
    """Tests for connecting and disconnecting this service."""

    async def test_disconnect_service(self):
        """Disconnecting sends the service entity ID to the disconnect endpoint."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"status": "disconnected"})

        await make_client(handler).disconnect_service("co-1")

        assert seen["method"] == "PUT"
        assert seen["path"] == (
            "/api/collaborations_services/v1/disconnect_collaboration_service/co-1"
        )
        assert seen["body"] == {"service_entity_id": "https://service.cloud.example.com"}


class TestResponseHandling:
    """Tests for responses that carry no usable body."""

    async def test_empty_body_where_data_is_expected(self):
        """A success response without a body is reported, not parsed into an empty object."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201)

        with pytest.raises(SRAMAPIError):
            await make_client(handler).create_collaboration(
                CollaborationCreate(
                    name="Cumulus research group",
                    description="Cumulus research group.",
                    administrators=["jdoe@uniharderwijk.nl"],
                )
            )

    async def test_invitations_carry_the_requested_role(self):
        """Invitations returned by a bulk invite report the role that was requested."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json=[
                    {"email": "rdoe@uniharderwijk.nl", "invitation_id": "inv-1", "status": "open"}
                ],
            )

        invitations = await make_client(handler).invite(
            "co-1", emails=["rdoe@uniharderwijk.nl"], role="admin"
        )

        assert invitations[0].intended_role == "admin"


class TestPathSafety:
    """Tests that caller-supplied values cannot re-target a request."""

    async def test_uid_cannot_escape_its_path_segment(self):
        """A uid containing traversal stays inside the collaboration it was sent for."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(204)

        await make_client(handler).remove_member(
            "co-1", "../../co-2/members/victim-uid@sram.eduteams.org"
        )

        assert seen["path"].startswith("/api/collaborations/v1/co-1/members/")
        assert "/co-2/" not in seen["path"]

    async def test_group_uid_cannot_escape_its_path_segment(self):
        """A group member uid containing traversal stays inside its group."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(204)

        await make_client(handler).remove_group_member("group-1", "../../group-2/members/victim")

        assert seen["path"].startswith("/api/groups/v1/group-1/members/")
        assert "/group-2/" not in seen["path"]

    async def test_identifier_cannot_escape_its_path_segment(self):
        """A collaboration identifier containing traversal cannot reach another endpoint."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(200, json=COLLABORATION_DETAIL)

        await make_client(handler).get_collaboration("../../organisations/v1")

        assert seen["path"].startswith("/api/collaborations/v1/")
        assert not seen["path"].endswith("/organisations/v1")

    async def test_bare_dot_segments_are_refused(self):
        """A value that is only dots cannot collapse a path segment."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be sent")

        with pytest.raises(SRAMAPIError):
            await make_client(handler).remove_member("co-1", "..")

    async def test_invitation_identifier_cannot_escape_its_path_segment(self):
        """An invitation identifier containing traversal cannot reach another endpoint."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.raw_path.decode()
            return httpx.Response(204)

        await make_client(handler).withdraw_invitation("../collaborations/v1/co-2")

        assert seen["path"].startswith("/api/invitations/v1/")
        assert "/collaborations/" not in seen["path"]
