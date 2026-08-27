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
class Invitation:
    """An open invitation to join a collaboration."""

    identifier: str
    email: str
    intended_role: str = "member"
    status: str | None = None
    expiry_date: int | None = None

    @classmethod
    def from_open_invitation(cls, data: dict) -> "Invitation":
        """Create an invitation from an entry of the open invitations list."""
        invitation = data.get("invitation") or {}
        return cls(
            identifier=invitation.get("identifier", ""),
            email=invitation.get("email", ""),
            intended_role=data.get("intended_role", "member"),
            status=data.get("status"),
            expiry_date=invitation.get("expiry_date"),
        )

    @classmethod
    def from_created(cls, data: dict) -> "Invitation":
        """Create an invitation from the response to a bulk invite."""
        return cls(
            identifier=data.get("invitation_id", ""),
            email=data.get("email", ""),
            status=data.get("status"),
            expiry_date=data.get("invitation_expiry_date"),
        )


@dataclass
class CollaborationCreate:
    """The attributes of a collaboration to be created.

    Attributes:
        name: Display name of the collaboration.
        description: What the collaboration is for.
        administrators: Email addresses that receive an admin invitation.
        short_name: Short identifier, generated by SRAM when omitted.
        website_url: Optional website of the collaboration.
        disable_join_requests: If true, users cannot request membership themselves.
        disclose_member_information: If true, member names are disclosed to other members.
        disclose_email_information: If true, member emails are disclosed to other members.
        expiry_date: Optional expiry of the collaboration in epoch seconds.
        message: Message included in the invitation email.
        administrator: Optional uid that becomes admin without an invitation.
        tags: Optional labels.
        units: Optional units of the organisation.
    """

    name: str
    description: str
    administrators: list[str]
    short_name: str | None = None
    website_url: str | None = None
    disable_join_requests: bool = True
    disclose_member_information: bool = True
    disclose_email_information: bool = False
    expiry_date: int | None = None
    message: str | None = None
    administrator: str | None = None
    tags: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        """Return the request body for the SRAM create endpoint.

        Optional values that were not given are omitted, so SRAM applies its own defaults.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "administrators": self.administrators,
            "disable_join_requests": self.disable_join_requests,
            "disclose_member_information": self.disclose_member_information,
            "disclose_email_information": self.disclose_email_information,
        }
        optional = {
            "short_name": self.short_name,
            "website_url": self.website_url,
            "expiry_date": self.expiry_date,
            "message": self.message,
            "administrator": self.administrator,
            "tags": self.tags or None,
            "units": self.units or None,
        }
        payload.update({key: value for key, value in optional.items() if value})
        return payload


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

    async def create_collaboration(self, spec: CollaborationCreate) -> Collaboration:
        """Create a collaboration in the organisation the API token belongs to.

        The new collaboration is not connected to any service yet; call
        :meth:`connect_service` to make it usable for this application.

        Args:
            spec: The attributes of the collaboration to create.
        """
        data = await self._request("POST", "/api/collaborations/v1", json=spec.to_payload())
        return Collaboration.from_api(data)

    async def connect_service(self, identifier: str, service_entity_id: str | None = None) -> None:
        """Connect a service to a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
            service_entity_id: Entity ID of the service to connect. Defaults to the
                entity ID of this application from the settings.

        Raises:
            SRAMNotConfiguredError: If no service entity ID is given or configured.
        """
        entity_id = service_entity_id or self.settings.sram_service_entity_id
        if not entity_id:
            raise SRAMNotConfiguredError(
                "No service entity ID configured. "
                "Set SRAM_SERVICE_ENTITY_ID to connect this service to a collaboration."
            )
        await self._request(
            "PUT",
            f"/api/collaborations_services/v1/connect_collaboration_service/{identifier}",
            json={"service_entity_id": entity_id},
        )

    async def disconnect_service(
        self, identifier: str, service_entity_id: str | None = None
    ) -> None:
        """Disconnect a service from a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
            service_entity_id: Entity ID of the service to disconnect. Defaults to the
                entity ID of this application from the settings.

        Raises:
            SRAMNotConfiguredError: If no service entity ID is given or configured.
        """
        entity_id = service_entity_id or self.settings.sram_service_entity_id
        if not entity_id:
            raise SRAMNotConfiguredError(
                "No service entity ID configured. "
                "Set SRAM_SERVICE_ENTITY_ID to disconnect this service from a collaboration."
            )
        await self._request(
            "PUT",
            f"/api/collaborations_services/v1/disconnect_collaboration_service/{identifier}",
            json={"service_entity_id": entity_id},
        )

    async def delete_collaboration(self, identifier: str) -> None:
        """Delete a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
        """
        await self._request("DELETE", f"/api/collaborations/v1/{identifier}")

    async def invite(
        self,
        identifier: str,
        emails: list[str],
        role: Role = "member",
        message: str | None = None,
        invitation_expiry_date: int | None = None,
        membership_expiry_date: int | None = None,
        groups: list[str] | None = None,
    ) -> list[Invitation]:
        """Invite users to a collaboration by email.

        The invitees receive an email from SRAM and become members once they accept.

        Args:
            identifier: The collaboration's SRAM identifier.
            emails: Email addresses to invite.
            role: The role the invitees get on acceptance.
            message: Message included in the invitation email.
            invitation_expiry_date: Expiry of the invitation in epoch milliseconds.
            membership_expiry_date: Expiry of the membership in epoch milliseconds.
            groups: Identifiers of groups the invitees join on acceptance.

        Returns:
            One invitation per invited email address.
        """
        payload: dict[str, Any] = {
            "collaboration_identifier": identifier,
            "invites": emails,
            "intended_role": role,
        }
        optional = {
            "message": message,
            "invitation_expiry_date": invitation_expiry_date,
            "membership_expiry_date": membership_expiry_date,
            "groups": groups or None,
        }
        payload.update({key: value for key, value in optional.items() if value})

        data = await self._request("PUT", "/api/invitations/v1/collaboration_invites", json=payload)
        return [Invitation.from_created(item) for item in data or []]

    async def list_open_invitations(self, identifier: str) -> list[Invitation]:
        """List the open invitations of a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
        """
        data = await self._request("GET", f"/api/invitations/v1/invitations/{identifier}")
        return [Invitation.from_open_invitation(item) for item in data or []]

    async def resend_invitation(self, external_identifier: str) -> None:
        """Send an open invitation again.

        Args:
            external_identifier: The invitation's external identifier.
        """
        await self._request("PUT", f"/api/invitations/v1/resend/{external_identifier}")

    async def update_invitation(
        self,
        external_identifier: str,
        role: Role | None = None,
        groups: list[str] | None = None,
    ) -> None:
        """Change the intended role or target groups of an open invitation.

        Args:
            external_identifier: The invitation's external identifier.
            role: The new intended role.
            groups: Identifiers of the groups the invitee joins on acceptance.
        """
        payload: dict[str, Any] = {}
        if role:
            payload["intended_role"] = role
        if groups is not None:
            payload["groups"] = groups
        await self._request(
            "PATCH", f"/api/invitations/v1/update/{external_identifier}", json=payload
        )

    async def withdraw_invitation(self, external_identifier: str) -> None:
        """Withdraw an open invitation.

        Args:
            external_identifier: The invitation's external identifier.
        """
        await self._request("DELETE", f"/api/invitations/v1/{external_identifier}")

    async def set_member_role(self, identifier: str, uid: str, role: Role) -> None:
        """Set a member's role in a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
            uid: The member's SRAM uid.
            role: The new role, admin or member.
        """
        await self._request(
            "PUT",
            f"/api/collaborations/v1/{identifier}/members",
            json={"uid": uid, "role": role},
        )

    async def remove_member(self, identifier: str, uid: str) -> None:
        """Remove a member from a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
            uid: The member's SRAM uid.
        """
        await self._request("DELETE", f"/api/collaborations/v1/{identifier}/members/{uid}")

    async def create_group(
        self,
        identifier: str,
        name: str,
        short_name: str,
        description: str | None = None,
        auto_provision_members: bool = False,
    ) -> Group:
        """Create a group inside a collaboration.

        Args:
            identifier: The collaboration's SRAM identifier.
            name: Display name of the group.
            short_name: Short identifier, used in the group's entitlement.
            description: What the group is for.
            auto_provision_members: If true, every collaboration member joins this group.

        Returns:
            The created group.
        """
        payload: dict[str, Any] = {
            "collaboration_identifier": identifier,
            "name": name,
            "short_name": short_name,
            "auto_provision_members": auto_provision_members,
        }
        if description:
            payload["description"] = description
        data = await self._request("POST", "/api/groups/v1", json=payload)
        return Group.from_api(data)

    async def update_group(
        self,
        group_identifier: str,
        name: str | None = None,
        description: str | None = None,
        auto_provision_members: bool | None = None,
    ) -> Group:
        """Update the properties of a group.

        Args:
            group_identifier: The group's SRAM identifier.
            name: New display name.
            description: New description.
            auto_provision_members: New auto provisioning setting.

        Returns:
            The updated group.
        """
        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        if description:
            payload["description"] = description
        if auto_provision_members is not None:
            payload["auto_provision_members"] = auto_provision_members
        data = await self._request("PUT", f"/api/groups/v1/{group_identifier}", json=payload)
        return Group.from_api(data)

    async def delete_group(self, group_identifier: str) -> None:
        """Delete a group.

        Args:
            group_identifier: The group's SRAM identifier.
        """
        await self._request("DELETE", f"/api/groups/v1/{group_identifier}")

    async def add_group_member(self, group_identifier: str, uid: str) -> None:
        """Add a collaboration member to a group.

        Args:
            group_identifier: The group's SRAM identifier.
            uid: The member's SRAM uid.
        """
        await self._request("POST", f"/api/groups/v1/{group_identifier}", json={"uid": uid})

    async def remove_group_member(self, group_identifier: str, uid: str) -> None:
        """Remove a member from a group.

        Args:
            group_identifier: The group's SRAM identifier.
            uid: The member's SRAM uid.
        """
        await self._request("DELETE", f"/api/groups/v1/{group_identifier}/members/{uid}")

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
