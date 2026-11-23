"""SCIM pull client for SRAM.

Reads the users and groups of all collaborations connected to the service from
SRAM's SCIM endpoints, authenticated with the service's SCIM pull token.
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
from fastapi import Depends

from sram_fastapi.collaborations import SRAMAPIError
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)

GROUP_EXTENSION = "urn:mace:surf.nl:sram:scim:extension:Group"


class SCIMNotConfiguredError(SRAMAPIError):
    """Raised when no SCIM pull token is configured."""


class SCIMTokenError(SRAMAPIError):
    """Raised when SRAM rejects the SCIM pull token."""


@dataclass(frozen=True)
class SCIMUser:
    """A SRAM user as returned by SCIM.

    Attributes:
        id: SCIM resource id, referenced by group memberships.
        user_name: The SRAM username.
        display_name: Display name, falling back to the formatted name.
        email: Primary email address, or the first one listed.
    """

    id: str
    user_name: str
    display_name: str | None
    email: str | None

    @classmethod
    def from_resource(cls, resource: dict[str, Any]) -> "SCIMUser":
        """Build a user from a SCIM User resource."""
        emails = resource.get("emails") or []
        primary = next((e for e in emails if e.get("primary")), emails[0] if emails else {})
        return cls(
            id=resource["id"],
            user_name=resource.get("userName", ""),
            display_name=resource.get("displayName")
            or (resource.get("name") or {}).get("formatted"),
            email=primary.get("value"),
        )


@dataclass(frozen=True)
class SCIMGroup:
    """A SRAM collaboration or group as returned by SCIM.

    Attributes:
        id: SCIM resource id.
        display_name: The group's display name.
        urn: SRAM URN from the SRAM group extension.
        member_ids: SCIM ids of the member users.
    """

    id: str
    display_name: str
    urn: str | None
    member_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_resource(cls, resource: dict[str, Any]) -> "SCIMGroup":
        """Build a group from a SCIM Group resource."""
        return cls(
            id=resource["id"],
            display_name=resource.get("displayName", ""),
            urn=(resource.get(GROUP_EXTENSION) or {}).get("urn"),
            member_ids=[m["value"] for m in resource.get("members") or [] if "value" in m],
        )


@dataclass(frozen=True)
class SCIMDirectory:
    """All users and groups SRAM provisions to this service."""

    users: list[SCIMUser]
    groups: list[SCIMGroup]

    def member_names(self, group: SCIMGroup) -> list[str]:
        """Return the user names of a group's members.

        Args:
            group: A group of this directory.

        Returns:
            User names in membership order; the SCIM id for members not in the user list.
        """
        by_id = {u.id: u.user_name for u in self.users}
        return [by_id.get(member_id, member_id) for member_id in group.member_ids]


class SRAMSCIMClient:
    """Client for SRAM's SCIM pull endpoints."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """Initialise the client.

        Args:
            settings: Application settings.
            transport: Optional httpx transport, used by tests to serve requests locally.
        """
        self.settings = settings
        self._transport = transport

    @property
    def configured(self) -> bool:
        """Whether a SCIM pull token is configured."""
        return bool(self.settings.sram_scim_token)

    async def get_directory(self) -> SCIMDirectory:
        """Read all users and groups from SRAM.

        Returns:
            The users and groups of all collaborations connected to the service.

        Raises:
            SCIMNotConfiguredError: If no SCIM pull token is configured.
            SCIMTokenError: If SRAM rejects the token.
            SRAMAPIError: If SRAM cannot be reached or fails.
        """
        if not self.configured:
            raise SCIMNotConfiguredError("SCIM pull is not configured. Set SRAM_SCIM_TOKEN.")
        async with httpx.AsyncClient(transport=self._transport) as client:
            users = await self._list(client, "Users")
            groups = await self._list(client, "Groups")
        return SCIMDirectory(
            users=[SCIMUser.from_resource(r) for r in users],
            groups=[SCIMGroup.from_resource(r) for r in groups],
        )

    async def _list(self, client: httpx.AsyncClient, resource_type: str) -> list[dict[str, Any]]:
        """Fetch the resources of one SCIM endpoint."""
        path = f"/api/scim/v2/{resource_type}"
        url = f"{self.settings.sram_api_base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {self.settings.sram_scim_token}",
            "Accept": "application/scim+json, application/json",
        }
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise SRAMAPIError(f"SRAM SCIM request failed: {exc}") from exc
        if response.status_code in (401, 403):
            logger.error("SRAM rejected the SCIM pull token on %s", path)
            raise SCIMTokenError("SRAM rejected the SCIM pull token.")
        if not response.is_success:
            raise SRAMAPIError(f"SRAM SCIM endpoint returned {response.status_code} for {path}")
        return response.json().get("Resources") or []


def get_scim_client(settings: Annotated[Settings, Depends(get_settings)]) -> SRAMSCIMClient:
    """Get the SCIM pull client as a FastAPI dependency."""
    return SRAMSCIMClient(settings)
