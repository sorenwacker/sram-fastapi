"""Page routes for the demo application.

This module provides the HTML page endpoints for the SRAM demo application.
All content is consolidated into a single home page with clear sections
for identity, access rights, and API usage.
"""

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import User, get_optional_user, get_token_user
from sram_fastapi.config import Settings, get_settings

from .authorization import DEMO_REQUIRED_AFFILIATION, DEMO_REQUIRED_ENTITLEMENT

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def create_pages_router() -> APIRouter:
    """Create the pages router for HTML endpoints.

    Returns:
        APIRouter with routes for the home page, legal pages, and API endpoints.
    """
    router = APIRouter(tags=["pages"])
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @router.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        """Render the home page.

        Shows different content based on authentication state:
        - Unauthenticated: Login prompt and explanation of SRAM
        - Authenticated: User identity, access rights, and testing tools
        """
        raw_claims_json = ""
        if user:
            raw_claims_json = json.dumps(user.raw_claims, indent=2, default=str)

        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "user": user,
                "base_url": settings.base_url,
                "raw_claims_json": raw_claims_json,
                "required_entitlement": DEMO_REQUIRED_ENTITLEMENT,
                "required_affiliation": DEMO_REQUIRED_AFFILIATION,
            },
        )

    @router.get("/privacy", response_class=HTMLResponse)
    async def privacy(request: Request):
        """Privacy policy page."""
        return templates.TemplateResponse(
            request=request,
            name="privacy.html",
            context={"user": None},
        )

    @router.get("/aup", response_class=HTMLResponse)
    async def aup(request: Request):
        """Acceptable use policy page."""
        return templates.TemplateResponse(
            request=request,
            name="aup.html",
            context={"user": None},
        )

    @router.get("/api/protected")
    async def protected_api(
        user: Annotated[User, Depends(get_token_user)],
    ) -> dict:
        """Protected API endpoint (requires Bearer token)."""
        return {
            "message": "You have access to the protected API",
            "user": {
                "sub": user.sub,
                "email": user.email,
                "name": user.name,
            },
        }

    @router.get("/health")
    async def health() -> dict:
        """Health check."""
        return {"status": "healthy"}

    return router
