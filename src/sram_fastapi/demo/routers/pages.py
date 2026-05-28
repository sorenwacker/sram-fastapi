"""Page routes for the demo application.

This module provides the HTML page endpoints for the SRAM demo application.
All content is consolidated into a single home page with clear sections
for identity, access rights, and API usage.
"""

import json
import time
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from sram_fastapi import __version__
from sram_fastapi.auth import OIDCClient, User, get_oidc_client, get_optional_user, get_token_user
from sram_fastapi.config import Settings, get_settings

from .authorization import DEMO_REQUIRED_AFFILIATION, DEMO_REQUIRED_ENTITLEMENT


class UserInfo(BaseModel):
    """User information returned by protected API."""

    sub: str
    email: str | None
    name: str | None


class ProtectedAPIResponse(BaseModel):
    """Response model for protected API endpoint."""

    message: str
    user: UserInfo


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    status: str


class HelloResponse(BaseModel):
    """Response model for hello endpoint."""

    message: str
    user: str
    email: str | None = None
    validation_time_ms: float | None = None


class TokenValidationResponse(BaseModel):
    """Response model for token validation endpoint."""

    active: bool
    status: str | None = None
    detail: str | None = None
    email: str | None = None
    name: str | None = None
    sub: str | None = None
    exp: int | None = None
    validation_time_ms: float | None = None

    model_config = {"extra": "allow"}


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
                "version": __version__,
            },
        )

    @router.get("/privacy", response_class=HTMLResponse)
    async def privacy(request: Request):
        """Privacy policy page."""
        return templates.TemplateResponse(
            request=request,
            name="privacy.html",
            context={"user": None, "version": __version__},
        )

    @router.get("/aup", response_class=HTMLResponse)
    async def aup(request: Request):
        """Acceptable use policy page."""
        return templates.TemplateResponse(
            request=request,
            name="aup.html",
            context={"user": None, "version": __version__},
        )

    @router.get("/api/protected", response_model=ProtectedAPIResponse)
    async def protected_api(
        user: Annotated[User, Depends(get_token_user)],
    ) -> ProtectedAPIResponse:
        """Protected API endpoint (requires Bearer token)."""
        return ProtectedAPIResponse(
            message="You have access to the protected API",
            user=UserInfo(sub=user.sub, email=user.email, name=user.name),
        )

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check."""
        return HealthResponse(status="healthy")

    @router.get("/api/hello", response_model=HelloResponse)
    async def hello(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> HelloResponse:
        """Protected hello endpoint using SRAM application tokens.

        Requires a valid SRAM application token in the Authorization header.
        """
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.removeprefix("Bearer ").strip()

        if not settings.sram_introspection_token:
            raise HTTPException(
                status_code=500,
                detail="SRAM introspection not configured",
            )

        start_time = time.perf_counter()
        try:
            result = await oidc_client.introspect_sram_token(token)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Token introspection failed: {e}")

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        if not result.get("active"):
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token: {result.get('status', 'unknown')}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return HelloResponse(
            message="Hello World!",
            user=result.get("name") or result.get("sub", "unknown"),
            email=result.get("email"),
            validation_time_ms=round(elapsed_ms, 1),
        )

    @router.get("/test-token", response_class=HTMLResponse)
    async def test_token_page(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        settings: Annotated[Settings, Depends(get_settings)],
    ):
        """Render the token testing page."""
        access_token = request.session.get("access_token") if user else None

        return templates.TemplateResponse(
            request=request,
            name="test_token.html",
            context={
                "user": user,
                "access_token": access_token,
                "introspection_configured": bool(settings.sram_introspection_token),
                "introspection_url": settings.sram_introspection_url,
                "base_url": settings.base_url,
                "version": __version__,
            },
        )

    @router.post("/test-token/validate", response_model=TokenValidationResponse)
    async def validate_token(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> TokenValidationResponse:
        """Validate a SRAM application token via introspection."""
        body = await request.json()
        token = body.get("token", "").strip()

        if not token:
            return TokenValidationResponse(
                active=False, status="token-missing", detail="No token provided"
            )

        if not settings.sram_introspection_token:
            return TokenValidationResponse(
                active=False,
                status="not-configured",
                detail="SRAM introspection token not configured",
            )

        start_time = time.perf_counter()
        try:
            result = await oidc_client.introspect_sram_token(token)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return TokenValidationResponse(**result, validation_time_ms=round(elapsed_ms, 1))
        except httpx.HTTPError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return TokenValidationResponse(
                active=False,
                status="http-error",
                detail=str(e),
                validation_time_ms=round(elapsed_ms, 1),
            )
        except ValueError as e:
            return TokenValidationResponse(active=False, status="config-error", detail=str(e))

    return router
