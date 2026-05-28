"""SRAM OIDC authentication module."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import httpx
from authlib.integrations.starlette_client import OAuth
from authlib.oauth2.rfc6749 import OAuth2Token
from fastapi import Depends, HTTPException, Request, status
from starlette.config import Config
from starlette.responses import RedirectResponse

from sram_fastapi.config import Settings, get_settings


class AuthorizationError(Exception):
    """Raised when a user lacks required authorization."""

    def __init__(
        self,
        required: list[str],
        actual: list[str],
        check_type: str,
        require_all: bool = False,
    ):
        self.required = required
        self.actual = actual
        self.check_type = check_type
        self.require_all = require_all
        mode = "all" if require_all else "any"
        super().__init__(
            f"Access denied: missing required {check_type}s "
            f"(required {mode} of {required}, has {actual})"
        )


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
                    "scope": (
                        "openid email profile "
                        "eduperson_entitlement voperson_external_affiliation"
                    ),
                },
            )
        return self._oauth

    async def authorize_redirect(self, request: Request, redirect_uri: str) -> RedirectResponse:
        """Redirect to SRAM authorization endpoint.

        This method properly saves OAuth state to the session for CSRF protection.
        """
        oauth = self.get_oauth()
        return await oauth.sram.authorize_redirect(request, redirect_uri)

    async def handle_callback(self, request: Request) -> tuple[OAuth2Token, User]:
        """Handle OIDC callback and return token and user info."""
        oauth = self.get_oauth()
        token = await oauth.sram.authorize_access_token(request)

        # Always fetch from userinfo endpoint to get full claims
        # including eduperson_entitlement and voperson_external_affiliation
        userinfo = await oauth.sram.userinfo(token=token)

        user = User.from_claims(dict(userinfo))
        return token, user

    async def introspect_token(self, token: str) -> dict | None:
        """Introspect a token using SRAM's OIDC introspection endpoint.

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

    async def introspect_sram_token(self, token: str) -> dict:
        """Introspect a SRAM application token.

        Uses the SRAM token introspection endpoint which requires an
        application introspection token for authentication.

        Returns:
            dict with introspection result including 'active' and 'status' fields.
            Possible status values: 'token-valid', 'token-unknown', 'token-expired',
            'user-suspended', 'token-not-connected'.

        Raises:
            ValueError: If introspection token is not configured.
            httpx.HTTPError: If the request fails.
        """
        if not self.settings.sram_introspection_token:
            raise ValueError("SRAM introspection token not configured")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.settings.sram_introspection_url,
                data={"token": token},
                headers={
                    "Authorization": f"Bearer {self.settings.sram_introspection_token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()
            return response.json()


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


def _match_affiliation(pattern: str, affiliation: str) -> bool:
    """Check if an affiliation matches a pattern.

    Supports wildcards:
    - "role@" matches any organization with that role
    - "@org" matches any role at that organization
    - "role@org" requires exact match
    """
    if pattern.endswith("@"):
        # Wildcard: role@ matches any org
        role_prefix = pattern[:-1]
        return affiliation.startswith(role_prefix + "@")
    elif pattern.startswith("@"):
        # Wildcard: @org matches any role
        org_suffix = pattern[1:]
        return affiliation.endswith("@" + org_suffix)
    else:
        # Exact match
        return pattern == affiliation


def _create_authorization_check(
    required: tuple[str, ...],
    check_type: str,
    require_all: bool,
    get_user_values: Callable[[User], list[str]],
    match_func: Callable[[str, str], bool],
) -> Callable:
    """Base factory for creating authorization dependency checks.

    Args:
        required: Values to check against
        check_type: Type of check ("entitlement" or "affiliation")
        require_all: If True, all values must match; if False, any match suffices
        get_user_values: Function to extract values from User
        match_func: Function to match a required value against a user value

    Returns:
        A dependency function that validates authorization and returns the User.
    """

    def check_authorization(user: User = Depends(get_current_user)) -> User:
        user_values = get_user_values(user)

        def matches(req: str) -> bool:
            return any(match_func(req, val) for val in user_values)

        if require_all:
            has_required = all(matches(r) for r in required)
        else:
            has_required = any(matches(r) for r in required)

        if not has_required:
            raise AuthorizationError(
                required=list(required),
                actual=user_values,
                check_type=check_type,
                require_all=require_all,
            )

        return user

    return check_authorization


def require_entitlement(*required: str, require_all: bool = False) -> Callable:
    """Create a dependency that requires specific entitlements.

    Args:
        *required: One or more entitlement URIs to check
        require_all: If True, user must have ALL entitlements. If False (default),
                     user needs at least ONE of the entitlements.

    Returns:
        A dependency function that validates entitlements and returns the User.

    Raises:
        AuthorizationError: If the user lacks required entitlements.
    """
    return _create_authorization_check(
        required=required,
        check_type="entitlement",
        require_all=require_all,
        get_user_values=lambda u: u.eduperson_entitlement or [],
        match_func=lambda req, val: req == val,
    )


def require_affiliation(*required: str, require_all: bool = False) -> Callable:
    """Create a dependency that requires specific affiliations.

    Supports wildcard matching:
    - "role@" matches any organization with that role (e.g., "staff@" matches "staff@tudelft.nl")
    - "@org" matches any role at that organization (e.g., "@tudelft.nl" matches "staff@tudelft.nl")

    Args:
        *required: One or more affiliation patterns to check
        require_all: If True, user must match ALL patterns. If False (default),
                     user needs to match at least ONE pattern.

    Returns:
        A dependency function that validates affiliations and returns the User.

    Raises:
        AuthorizationError: If the user lacks required affiliations.
    """
    return _create_authorization_check(
        required=required,
        check_type="affiliation",
        require_all=require_all,
        get_user_values=lambda u: u.voperson_external_affiliation or [],
        match_func=_match_affiliation,
    )
