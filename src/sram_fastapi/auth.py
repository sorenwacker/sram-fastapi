"""SRAM OIDC authentication module."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
from authlib.integrations.starlette_client import OAuth
from authlib.oauth2.rfc6749 import OAuth2Token
from fastapi import Depends, HTTPException, Request, status
from starlette.config import Config
from starlette.responses import RedirectResponse

from sram_fastapi.collaborations import (
    ServiceGroupIndex,
    SRAMOrganisationClient,
    get_organisation_client,
    get_service_group_index,
    groups_of,
)
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

    sub: str  # Subject claim; SURFconext-generated for browser logins
    voperson_id: str | None = None
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
            voperson_id=claims.get("voperson_id"),
            email=claims.get("email"),
            name=claims.get("name"),
            preferred_username=claims.get("preferred_username"),
            eduperson_entitlement=claims.get("eduperson_entitlement"),
            voperson_external_affiliation=claims.get("voperson_external_affiliation"),
            raw_claims=claims,
        )

    @property
    def identifier(self) -> str:
        """The SRAM identifier to recognise this user by.

        Browser logins through SURFconext EngineBlock carry the SRAM uid in
        ``voperson_id``; their ``sub`` is unrelated. Introspection answers carry no
        ``voperson_id`` and hold the SRAM uid in ``sub``.

        Returns:
            ``voperson_id`` when present, otherwise ``sub``.
        """
        return self.voperson_id or self.sub


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
                # EngineBlock releases all entitled claims for "openid" alone and
                # rejects logins that request unknown scopes
                # authlib does not URL-encode the secret for HTTP Basic (RFC 6749
                # 2.3.1), which breaks secrets with special characters; the form
                # body is encoded correctly
                client_kwargs={
                    "scope": "openid",
                    "token_endpoint_auth_method": "client_secret_post",
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


def get_oidc_client(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> OIDCClient:
    """Return the application's OIDC client, creating it from its settings on first use.

    The client caches the discovery document, so it is kept per application on
    ``app.state`` rather than rebuilt per request.

    Args:
        request: The current request, whose application holds the client.
        settings: The application's settings.

    Returns:
        The OIDC client built from these settings.
    """
    client = getattr(request.app.state, "sram_oidc_client", None)
    if client is None or client.settings is not settings:
        client = OIDCClient(settings)
        request.app.state.sram_oidc_client = client
    return client


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


def is_collaboration_manager(user: User, settings: Settings) -> bool:
    """Whether the user holds the entitlement configured as collaboration manager.

    Args:
        user: The authenticated user.
        settings: Settings naming the manager entitlement.

    Returns:
        False when no manager entitlement is configured.
    """
    required = settings.collaboration_manager_entitlement
    return bool(required) and required in (user.eduperson_entitlement or [])


def require_collaboration_manager(user: User, settings: Settings) -> None:
    """Raise unless the user is a collaboration manager.

    Args:
        user: The authenticated user.
        settings: Settings naming the manager entitlement.

    Raises:
        AuthorizationError: If the user does not hold the manager entitlement.
    """
    if is_collaboration_manager(user, settings):
        return
    raise AuthorizationError(
        required=[settings.collaboration_manager_entitlement or "collaboration manager"],
        actual=user.eduperson_entitlement or [],
        check_type="entitlement",
    )


@dataclass(frozen=True)
class SRAMTokenCheck:
    """The outcome of validating a SRAM application token.

    Attributes:
        introspection: SRAM's complete introspection answer for an active token.
        elapsed_ms: How long the introspection request took, in milliseconds.
    """

    introspection: dict[str, Any]
    elapsed_ms: float


async def require_sram_application_token(
    request: Request,
    oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SRAMTokenCheck:
    """Validate the SRAM application token in the Authorization header.

    Args:
        request: The incoming request.
        oidc_client: Client that performs the introspection.
        settings: Settings providing the service introspection token.

    Returns:
        The introspection answer for an active token.

    Raises:
        HTTPException: 401 without a token or for an inactive one, 500 when
            introspection is not configured, 503 when SRAM rejects the service
            introspection token, 502 when SRAM cannot be reached.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.sram_introspection_token:
        raise HTTPException(status_code=500, detail="SRAM introspection not configured")

    token = auth_header.removeprefix("Bearer ").strip()
    start_time = time.perf_counter()
    try:
        result = await oidc_client.introspect_sram_token(token)
    except IntrospectionTokenError as exc:
        raise HTTPException(
            status_code=503,
            detail="Service configuration error. Please contact the administrator.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Token introspection failed: {exc}") from exc
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    if not result.get("active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {result.get('status', 'unknown')}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return SRAMTokenCheck(introspection=result, elapsed_ms=elapsed_ms)


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


async def grants_feature(
    user: User,
    settings: Settings,
    feature: str,
    client: SRAMOrganisationClient | None = None,
    index: ServiceGroupIndex | None = None,
) -> bool:
    """Whether the user holds the group that grants a feature.

    A feature bound to a collaboration is granted by that collaboration's group, and needs
    nothing else. A feature naming only a short name is granted only where SRAM confirms
    that a service group of that name exists, because a short name is chosen by whoever
    creates the group: an admin of any collaboration connected to this service could
    otherwise create a group under that name and hand the feature to their members.

    Verification covers the organisation the API token belongs to. Without the token, or
    when SRAM cannot be reached, an unbound feature grants nothing.

    Args:
        user: The authenticated user.
        settings: Application settings holding the feature mapping.
        feature: The feature name to check.
        client: The organisation API client, needed to verify an unbound feature.
        index: The service group index, needed to verify an unbound feature.

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

    if client is None or index is None:
        return False

    verified = (await index.bindings(client)).get(group.short_name, set())
    if not verified:
        logger.error(
            "Feature '%s' names the group '%s', which SRAM does not report as a service "
            "group of this organisation, so it grants nothing. Define it as a service "
            "group, or bind the feature as SRAM_FEATURE_GROUPS=%s=<collaboration>/%s.",
            feature,
            group.short_name,
            feature,
            group.short_name,
        )
        return False

    return any(
        short_name == group.short_name and collaboration in verified
        for collaboration, short_name in held
    )


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

    async def check_groups(
        user: Annotated[User, Depends(get_current_user)],
        settings: Annotated[Settings, Depends(get_settings)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        index: Annotated[ServiceGroupIndex, Depends(get_service_group_index)],
    ) -> User:
        granted = [
            await grants_feature(user, settings, feature, client, index) for feature in features
        ]
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
