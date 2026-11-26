"""Checks of the service tokens the application presents to SRAM.

SRAM issues these tokens in its web interface and offers no protocol to renew them, so
the application can only test whether SRAM still accepts each one. Every check makes a
single read-only request and classifies SRAM's answer.
"""

import secrets
from typing import Annotated, Literal

import httpx
from fastapi import Depends

from sram_fastapi.config import Settings, get_settings

TokenName = Literal["pull", "introspection", "organisation"]
TokenStatus = Literal["valid", "rejected", "unreachable", "not-configured"]


class TokenChecker:
    """Tests SRAM service tokens with one request each."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """Initialise the checker.

        Args:
            settings: Application settings holding the tokens.
            transport: Optional httpx transport, used by tests to serve requests locally.
        """
        self.settings = settings
        self._transport = transport

    def _request(self, name: TokenName) -> httpx.Request | None:
        """Build the check request for a token, or None when it is not configured."""
        base = self.settings.sram_api_base_url.rstrip("/")
        if name == "pull" and (token := self.settings.sram_scim_token):
            return httpx.Request(
                "GET",
                f"{base}/api/scim/v2/Users",
                params={"count": 1},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/scim+json"},
            )
        if name == "introspection" and (token := self.settings.sram_introspection_token):
            return httpx.Request(
                "POST",
                self.settings.sram_introspection_url,
                data={"token": secrets.token_urlsafe(16)},
                headers={"Authorization": f"Bearer {token}"},
            )
        if name == "organisation" and (token := self.settings.sram_organisation_api_token):
            return httpx.Request(
                "GET", f"{base}/api/organisations/v1", headers={"Authorization": f"bearer {token}"}
            )
        return None

    async def check(self, name: TokenName) -> TokenStatus:
        """Test whether SRAM accepts a token.

        Args:
            name: Which token to test.

        Returns:
            ``valid`` when SRAM accepts it, ``rejected`` on 401 or 403, ``unreachable``
            when SRAM cannot be reached or fails, ``not-configured`` when no token is set.
        """
        request = self._request(name)
        if request is None:
            return "not-configured"
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10) as client:
                response = await client.send(request)
        except httpx.HTTPError:
            return "unreachable"
        if response.status_code in (401, 403):
            return "rejected"
        if not response.is_success:
            return "unreachable"
        if name == "introspection":
            try:
                return "valid" if response.json().get("active") is False else "unreachable"
            except ValueError:
                return "unreachable"
        return "valid"


def get_token_checker(settings: Annotated[Settings, Depends(get_settings)]) -> TokenChecker:
    """Get the token checker as a FastAPI dependency."""
    return TokenChecker(settings)
