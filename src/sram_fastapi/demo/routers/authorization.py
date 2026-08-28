"""Authorization demo routes.

This module provides API endpoints that demonstrate authorization based on
SRAM attributes (entitlements and affiliations). These endpoints return JSON
and are designed to be called via fetch() from the home page for inline
testing of access control.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sram_fastapi.auth import User, require_affiliation, require_entitlement, require_group
from sram_fastapi.config import Settings

# Demo authorization requirements
# These values should be configured per-deployment in a real application
DEMO_REQUIRED_ENTITLEMENT = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemogroup"
DEMO_REQUIRED_AFFILIATION = "employee@"


class AuthorizationCheckResponse(BaseModel):
    """Response model for authorization check endpoints."""

    message: str
    user: str
    required: str


def _add_feature_route(router: APIRouter, feature: str, short_name: str) -> None:
    """Add one route protected by the group that grants a feature.

    Args:
        router: The router to add the route to.
        feature: The feature name from the configuration.
        short_name: The SRAM group short name that grants it.
    """

    @router.get(
        f"/features/{feature}",
        response_model=AuthorizationCheckResponse,
        name=f"feature_{feature}",
    )
    async def feature_protected(
        user: Annotated[User, Depends(require_group(feature))],
    ) -> AuthorizationCheckResponse:
        """Endpoint reachable by members of the group that grants this feature."""
        return AuthorizationCheckResponse(
            message=f"Group check passed for feature '{feature}'",
            user=user.preferred_username or user.email or user.sub,
            required=short_name,
        )


def create_authorization_router(settings: Settings) -> APIRouter:
    """Create the authorization demo router.

    Args:
        settings: Application settings, read for the configured feature groups.

    Returns:
        APIRouter with JSON endpoints protected by entitlement, affiliation and group
        checks. One route is served per configured feature.
    """
    router = APIRouter(prefix="/demo", tags=["authorization"])

    @router.get("/entitlement-protected", response_model=AuthorizationCheckResponse)
    async def entitlement_protected(
        user: Annotated[User, Depends(require_entitlement(DEMO_REQUIRED_ENTITLEMENT))],
    ) -> AuthorizationCheckResponse:
        """Endpoint protected by entitlement requirement.

        Access is granted only if the user has the required entitlement
        in their eduperson_entitlement claim.

        Returns:
            JSON with access confirmation and user info.
        """
        return AuthorizationCheckResponse(
            message="Entitlement check passed",
            user=user.preferred_username or user.email or user.sub,
            required=DEMO_REQUIRED_ENTITLEMENT,
        )

    @router.get("/affiliation-protected", response_model=AuthorizationCheckResponse)
    async def affiliation_protected(
        user: Annotated[User, Depends(require_affiliation(DEMO_REQUIRED_AFFILIATION))],
    ) -> AuthorizationCheckResponse:
        """Endpoint protected by affiliation requirement.

        Access is granted if the user has an affiliation matching the
        required prefix (e.g., 'employee@' matches 'employee@tudelft.nl').

        Returns:
            JSON with access confirmation and user info.
        """
        return AuthorizationCheckResponse(
            message="Affiliation check passed",
            user=user.preferred_username or user.email or user.sub,
            required=DEMO_REQUIRED_AFFILIATION,
        )

    for feature, group in settings.feature_groups.items():
        _add_feature_route(router, feature, group.short_name)

    return router
