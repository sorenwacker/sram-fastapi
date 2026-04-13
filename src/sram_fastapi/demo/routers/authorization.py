"""Authorization demo routes.

This module provides API endpoints that demonstrate authorization based on
SRAM attributes (entitlements and affiliations). These endpoints return JSON
and are designed to be called via fetch() from the home page for inline
testing of access control.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sram_fastapi.auth import User, require_affiliation, require_entitlement

# Demo authorization requirements
# These values should be configured per-deployment in a real application
DEMO_REQUIRED_ENTITLEMENT = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemogroup"
DEMO_REQUIRED_AFFILIATION = "employee@"


class AuthorizationCheckResponse(BaseModel):
    """Response model for authorization check endpoints."""

    message: str
    user: str
    required: str


def create_authorization_router() -> APIRouter:
    """Create the authorization demo router.

    Returns:
        APIRouter with JSON endpoints protected by entitlement and affiliation checks.
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

    return router
