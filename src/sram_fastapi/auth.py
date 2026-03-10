"""SRAM OIDC authentication module."""

from dataclasses import dataclass
from typing import Annotated

import httpx
from authlib.integrations.starlette_client import OAuth
from authlib.oauth2.rfc6749 import OAuth2Token
from fastapi import Depends, HTTPException, Request, status
from starlette.config import Config

from sram_fastapi.config import Settings, get_settings


@dataclass
class User:
    """Authenticated user information from SRAM."""

    sub: str  # Subject identifier
    email: str | None = None
    name: str | None = None
    preferred_username: str | None = None
    eduperson_entitlement: list[str] | None = None
    voperson_external_affiliation: list[str] | None = None
    raw_claims: dict | None = None

    @classmethod
    def from_claims(cls, claims: dict) -> "User":
        """Create User from OIDC claims."""
        return cls(
            sub=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name"),
            preferred_username=claims.get("preferred_username"),
            eduperson_entitlement=claims.get("eduperson_entitlement"),
            voperson_external_affiliation=claims.get("voperson_external_affiliation"),
            raw_claims=claims,
        )


class OIDCClient:
    """OIDC client for SRAM authentication."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._oauth: OAuth | None = None
        self._discovery_document: dict | None = None

    async def get_discovery_document(self) -> dict:
        """Fetch OIDC discovery document."""
        if self._discovery_document is None:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.settings.sram_oidc_discovery_url)
                response.raise_for_status()
                self._discovery_document = response.json()
        return self._discovery_document

    def get_oauth(self) -> OAuth:
        """Get or create OAuth client."""
        if self._oauth is None:
            config = Config(
                environ={
                    "SRAM_CLIENT_ID": self.settings.sram_oidc_client_id,
                    "SRAM_CLIENT_SECRET": self.settings.sram_oidc_client_secret,
                }
            )
            self._oauth = OAuth(config)
            self._oauth.register(
                name="sram",
                server_metadata_url=self.settings.sram_oidc_discovery_url,
                client_kwargs={
                    "scope": "openid email profile",
                },
            )
        return self._oauth

    async def get_authorization_url(self, request: Request, redirect_uri: str) -> str:
        """Generate authorization URL for SRAM login."""
        oauth = self.get_oauth()
        redirect = await oauth.sram.create_authorization_url(redirect_uri)
        # Store state and nonce in session
        request.session["oauth_state"] = redirect.get("state")
        request.session["oauth_nonce"] = redirect.get("nonce")
        return redirect["url"]

    async def handle_callback(self, request: Request) -> tuple[OAuth2Token, User]:
        """Handle OIDC callback and return token and user info."""
        oauth = self.get_oauth()
        token = await oauth.sram.authorize_access_token(request)

        userinfo = token.get("userinfo")
        if userinfo is None:
            # Fetch userinfo if not included in token response
            userinfo = await oauth.sram.userinfo(token=token)

        user = User.from_claims(dict(userinfo))
        return token, user

    async def introspect_token(self, token: str) -> dict | None:
        """Introspect a token using SRAM's introspection endpoint.

        Returns the token info if valid and active, None otherwise.
        """
        try:
            discovery = await self.get_discovery_document()
        except httpx.HTTPError:
            return None

        introspection_endpoint = discovery.get("introspection_endpoint")
        if not introspection_endpoint:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    introspection_endpoint,
                    data={"token": token},
                    auth=(
                        self.settings.sram_oidc_client_id,
                        self.settings.sram_oidc_client_secret,
                    ),
                )
                if not response.is_success:
                    return None

                result = response.json()
                if result.get("active"):
                    return result
                return None
        except httpx.HTTPError:
            return None


# Global OIDC client instance
_oidc_client: OIDCClient | None = None


def get_oidc_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OIDCClient:
    """Get OIDC client dependency."""
    global _oidc_client
    if _oidc_client is None:
        _oidc_client = OIDCClient(settings)
    return _oidc_client


def get_current_user(request: Request) -> User:
    """Get current authenticated user from session."""
    user_data = request.session.get("user")
    if user_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return User.from_claims(user_data)


def get_optional_user(request: Request) -> User | None:
    """Get current user if authenticated, None otherwise."""
    user_data = request.session.get("user")
    if user_data is None:
        return None
    return User.from_claims(user_data)


async def get_token_user(
    request: Request,
    oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
) -> User:
    """Get user from Bearer token (for API access)."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header.removeprefix("Bearer ")
    token_info = await oidc_client.introspect_token(token)

    if token_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return User.from_claims(token_info)
