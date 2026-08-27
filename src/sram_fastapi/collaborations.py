"""Client for the SRAM organisation API.

The organisation API manages collaborations of a single SRAM organisation:
creating them, reading their membership, inviting users, and changing roles.
It authenticates with an organisation API token, which is a server-side
administrator credential and is unrelated to the OIDC client credentials used
for login.

This module performs no authorization decisions. Callers are responsible for
deciding who may invoke which method.
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends

from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)

Role = Literal["admin", "member"]

ENTITLEMENT_PREFIX = "urn:mace:surf.nl:sram:group:"


def entitlement_for(global_urn: str) -> str:
    """Return the SRAM entitlement that corresponds to a collaboration.

    Args:
        global_urn: The collaboration's global URN, such as ``uniharderwijk:cumulusgrp``.

    Returns:
        The entitlement value as it appears in the user's ``eduperson_entitlement`` claim.
    """
    return f"{ENTITLEMENT_PREFIX}{global_urn}"


def collaboration_urns(entitlements: list[str] | None) -> set[str]:
    """Return the global URNs of the collaborations an entitlement list implies.

    Group entitlements carry a third segment; only the collaboration part is kept, so
    membership of a group inside a collaboration also implies membership of the
    collaboration itself.

    Args:
        entitlements: Values of the user's ``eduperson_entitlement`` claim.

    Returns:
        The set of collaboration global URNs, such as ``{"uniharderwijk:cumulusgrp"}``.
    """
    urns = set()
    for entitlement in entitlements or []:
        if not entitlement.startswith(ENTITLEMENT_PREFIX):
            continue
        segments = entitlement[len(ENTITLEMENT_PREFIX) :].split(":")
        if len(segments) >= 2:
            urns.add(":".join(segments[:2]))
    return urns


class SRAMAPIError(Exception):
    """Raised when the SRAM organisation API cannot be reached or fails."""


class SRAMNotConfiguredError(SRAMAPIError):
    """Raised when the organisation API token is not configured."""


class OrganisationTokenError(SRAMAPIError):
    """Raised when SRAM rejects the organisation API token.

    This indicates a server configuration issue that requires admin attention:
    the token is invalid, expired, or lacks rights on the requested object.
    """


class CollaborationNotFoundError(SRAMAPIError):
    """Raised when a collaboration, group or invitation does not exist."""


class CollaborationConflictError(SRAMAPIError):
    """Raised when SRAM reports a conflicting state, such as a duplicate."""


@dataclass
class Membership:
    """A user's membership of a collaboration."""

    uid: str | None
    role: str
    status: str | None = None
    expiry_date: int | None = None
    name: str | None = None
    email: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Membership":
        """Create a membership from a SRAM collaboration membership object."""
        user = data.get("user") or {}
        return cls(
            uid=user.get("uid"),
            role=data.get("role", "member"),
            status=data.get("status"),
            expiry_date=data.get("expiry_date"),
            name=user.get("name"),
            email=user.get("email"),
        )


@dataclass
class Group:
    """A group inside a collaboration."""

    identifier: str
    name: str
    short_name: str | None = None
    description: str | None = None
    global_urn: str | None = None
    member_uids: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "Group":
        """Create a group from a SRAM group object."""
        memberships = data.get("collaboration_memberships") or []
        return cls(
            identifier=data.get("identifier", ""),
            name=data.get("name", ""),
            short_name=data.get("short_name"),
            description=data.get("description"),
            global_urn=data.get("global_urn"),
            member_uids=[
                (m.get("user") or {}).get("uid")
                for m in memberships
                if (m.get("user") or {}).get("uid")
            ],
        )


@dataclass
class Service:
    """A service connected to a collaboration."""

    entity_id: str
    name: str

    @classmethod
    def from_api(cls, data: dict) -> "Service":
        """Create a service from a SRAM service object."""
        return cls(entity_id=data.get("entity_id", ""), name=data.get("name", ""))


@dataclass
class Collaboration:
    """A SRAM collaboration."""

    identifier: str
    name: str
    short_name: str | None = None
    description: str | None = None
    global_urn: str | None = None
    status: str | None = None
    website_url: str | None = None
    disable_join_requests: bool | None = None
    disclose_member_information: bool | None = None
    disclose_email_information: bool | None = None
    expiry_date: int | None = None
    invitations_count: int = 0
    memberships: list[Membership] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "Collaboration":
        """Create a collaboration from a SRAM collaboration object.

        Handles both the full detail payload and the shorter overview payload
        returned inside an organisation, which carries no memberships.
        """
        return cls(
            identifier=data.get("identifier", ""),
            name=data.get("name", ""),
            short_name=data.get("short_name"),
            description=data.get("description"),
            global_urn=data.get("global_urn"),
            status=data.get("status"),
            website_url=data.get("website_url"),
            disable_join_requests=data.get("disable_join_requests"),
            disclose_member_information=data.get("disclose_member_information"),
            disclose_email_information=data.get("disclose_email_information"),
            expiry_date=data.get("expiry_date"),
            invitations_count=data.get("invitations_count") or 0,
            memberships=[
                Membership.from_api(m) for m in data.get("collaboration_memberships") or []
            ],
            groups=[Group.from_api(g) for g in data.get("groups") or []],
            services=[Service.from_api(s) for s in data.get("services") or []],
        )

    def admin_uids(self) -> set[str]:
        """Return the uids of the collaboration's administrators."""
        return {m.uid for m in self.memberships if m.role == "admin" and m.uid}


@dataclass
class Organisation:
    """A SRAM organisation with the collaborations it owns."""

    identifier: str
    name: str
    short_name: str | None = None
    collaborations: list[Collaboration] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "Organisation":
        """Create an organisation from a SRAM organisation object."""
        return cls(
            identifier=data.get("identifier", ""),
            name=data.get("name", ""),
            short_name=data.get("short_name"),
            collaborations=[Collaboration.from_api(c) for c in data.get("collaborations") or []],
        )


class SRAMOrganisationClient:
    """Client for the SRAM organisation API."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """Initialise the client.

        Args:
            settings: Application settings holding the API base URL and token.
            transport: Optional httpx transport, used by tests to serve requests locally.
        """
        self.settings = settings
        self._transport = transport

    @property
    def configured(self) -> bool:
        """Whether an organisation API token is configured."""
        return bool(self.settings.sram_organisation_api_token)

    async def get_organisation(self) -> Organisation:
        """Get the organisation the API token belongs to, with its collaborations."""
        data = await self._request("GET", "/api/organisations/v1")
        return Organisation.from_api(data)

    async def get_collaboration(self, identifier: str) -> Collaboration:
        """Get a collaboration with its memberships, groups and connected services.

        Args:
            identifier: The collaboration's SRAM identifier.
        """
        data = await self._request("GET", f"/api/collaborations/v1/{identifier}")
        return Collaboration.from_api(data)

    async def list_members(self, identifier: str) -> list[Membership]:
        """List the members of a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
        """
        collaboration = await self.get_collaboration(identifier)
        return collaboration.memberships

    async def _request(self, method: str, path: str, json: dict | None = None) -> Any:
        """Send a request to the SRAM organisation API.

        Args:
            method: HTTP method.
            path: Path below the API base URL.
            json: Optional JSON body.

        Returns:
            The parsed response body, or None for an empty response.

        Raises:
            SRAMNotConfiguredError: If no organisation API token is configured.
            OrganisationTokenError: If SRAM rejects the token.
            CollaborationNotFoundError: If the object does not exist.
            CollaborationConflictError: If SRAM reports a conflict.
            SRAMAPIError: If the request fails for any other reason.
        """
        if not self.configured:
            raise SRAMNotConfiguredError(
                "SRAM organisation API token not configured. "
                "Set SRAM_ORGANISATION_API_TOKEN to enable collaboration management."
            )

        url = f"{self.settings.sram_api_base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"bearer {self.settings.sram_organisation_api_token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                response = await client.request(method, url, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise SRAMAPIError(f"SRAM organisation API request failed: {exc}") from exc

        self._raise_for_status(response, method, path)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
        """Map a SRAM error response onto an exception.

        Args:
            response: The response received from SRAM.
            method: HTTP method, used for logging.
            path: Request path, used for logging.
        """
        if response.is_success:
            return

        if response.status_code in (401, 403):
            logger.error(
                "SRAM rejected the organisation API token on %s %s. "
                "Admin action required: renew SRAM_ORGANISATION_API_TOKEN, "
                "or check that it belongs to the organisation owning this collaboration.",
                method,
                path,
            )
            raise OrganisationTokenError(
                "The organisation API token is invalid, expired, or not authorised "
                "for this collaboration. Please contact the administrator."
            )
        if response.status_code == 404:
            raise CollaborationNotFoundError(f"Not found in SRAM: {path}")
        if response.status_code == 409:
            raise CollaborationConflictError(f"SRAM reports a conflict for {path}")

        raise SRAMAPIError(f"SRAM organisation API returned {response.status_code} for {path}")


def get_organisation_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SRAMOrganisationClient:
    """Get the organisation API client as a FastAPI dependency."""
    return SRAMOrganisationClient(settings)
