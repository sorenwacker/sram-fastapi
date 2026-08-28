"""SRAM OIDC authentication module."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import httpx
from authlib.integrations.starlette_client import OAuth
from authlib.oauth2.rfc6749 import OAuth2Token
from fastapi import Depends, HTTPException, Request, status
from starlette.config import Config
from starlette.responses import RedirectResponse

from sram_fastapi.collaborations import groups_of
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)


class IntrospectionTokenError(Exception):
    """Raised when the service introspection token is invalid or expired.

    This indicates a server configuration issue that requires admin attention.
    """

    def __init__(self, message: str = "Service introspection token is invalid or expired"):
        self.message = message
        super().__init__(self.message)


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
                        "openid email profile eduperson_entitlement voperson_external_affiliation"
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
            IntrospectionTokenError: If the service introspection token is invalid.
            httpx.HTTPError: If the request fails for other reasons.
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

            if response.status_code in (401, 403):
                logger.error(
                    "SRAM introspection token is invalid or expired. "
                    "Admin action required: renew SRAM_INTROSPECTION_TOKEN. "
                    "Get a new token from SRAM service settings."
                )
                raise IntrospectionTokenError(
                    "Service introspection token is invalid or expired. "
                    "Please contact the administrator."
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


def _describe_feature(settings: Settings, feature: str) -> str:
    """Describe the group a feature needs, for an authorization error message."""
    group = settings.feature_groups.get(feature)
    if group is None:
        return f"{feature} (not configured)"
    if group.collaboration:
        return f"{group.collaboration}/{group.short_name}"
    return group.short_name


def grants_feature(user: User, settings: Settings, feature: str) -> bool:
    """Whether the user holds the group that grants a feature.

    A feature bound to a collaboration is granted only by that collaboration's group. An
    unbound feature is granted by the short name in any collaboration, which is only
    sound when this service owns the name: SRAM prefixes the service abbreviation to
    every group it provisions for a service, and a collaboration admin cannot create a
    group under a name a connected service already occupies. A short name without that
    prefix could be chosen by any collaboration admin, so it is refused rather than
    trusted across collaborations.

    Args:
        user: The authenticated user.
        settings: Application settings holding the feature mapping.
        feature: The feature name to check.

    Returns:
        True if the user holds a group that grants the feature.
    """
    group = settings.feature_groups.get(feature)
    if group is None:
        logger.error(
            "Feature '%s' is not mapped to a group in SRAM_FEATURE_GROUPS; "
            "access is denied until it is configured.",
            feature,
        )
        return False

    held = groups_of(user.eduperson_entitlement)
    if group.collaboration:
        return (group.collaboration, group.short_name) in held

    abbreviation = settings.sram_service_abbreviation
    if not abbreviation or not group.short_name.startswith(f"{abbreviation}-"):
        logger.error(
            "Feature '%s' names the group '%s', which does not belong to this service. "
            "Name a service group, which SRAM prefixes with '%s-', or bind the feature "
            "to one collaboration as 'collaboration_urn/%s'.",
            feature,
            group.short_name,
            abbreviation or "<SRAM_SERVICE_ABBREVIATION>",
            group.short_name,
        )
        return False

    return any(short_name == group.short_name for _, short_name in held)


def require_group(*features: str, require_all: bool = False) -> Callable:
    """Create a dependency that requires membership of the groups granting features.

    A feature is mapped to a SRAM group by ``SRAM_FEATURE_GROUPS``. Matching on the
    group rather than on a full entitlement lets one rule serve every collaboration the
    application is connected to, because the collaboration part of an entitlement differs
    per collaboration while a service group's short name is fixed by the service.

    A feature the deployment does not define, or one that names a group this service does
    not own, is denied to everyone. See :func:`grants_feature` for the rules.

    Args:
        *features: One or more feature names to check.
        require_all: If True, the user must hold all of them. If False (default), one
            of them suffices.

    Returns:
        A dependency function that validates the features and returns the User.

    Raises:
        AuthorizationError: If the user lacks the required group membership.
    """

    def check_groups(
        user: Annotated[User, Depends(get_current_user)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> User:
        granted = [grants_feature(user, settings, feature) for feature in features]
        has_required = all(granted) if require_all else any(granted)

        if not (features and has_required):
            raise AuthorizationError(
                required=[_describe_feature(settings, feature) for feature in features],
                actual=sorted(
                    f"{collaboration}/{short_name}"
                    for collaboration, short_name in groups_of(user.eduperson_entitlement)
                ),
                check_type="group",
                require_all=require_all,
            )
        return user

    return check_groups
