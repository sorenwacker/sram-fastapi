"""Page routes for the demo application."""

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import User, get_current_user, get_optional_user, get_token_user
from sram_fastapi.config import Settings, get_settings

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def create_pages_router() -> APIRouter:
    """Create pages router."""
    router = APIRouter(tags=["pages"])
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @router.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        """Home page."""
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "user": user,
                "base_url": settings.base_url,
            },
        )

    @router.get("/profile", response_class=HTMLResponse)
    async def profile(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
    ):
        """User profile page (requires authentication)."""
        raw_claims_json = json.dumps(user.raw_claims, indent=2, default=str)
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={
                "user": user,
                "raw_claims_json": raw_claims_json,
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
