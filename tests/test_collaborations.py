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
