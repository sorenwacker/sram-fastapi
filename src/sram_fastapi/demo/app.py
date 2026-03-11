"""Demo application with HTML templates for SRAM authentication."""

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sram_fastapi.auth import (
    OIDCClient,
    User,
    get_current_user,
    get_oidc_client,
    get_optional_user,
    get_token_user,
)
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_demo_app(settings: Settings | None = None) -> FastAPI:
    """Create demo application with HTML templates."""
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=f"{settings.app_name} Demo",
        description="Demo application for SRAM OIDC authentication",
        version="0.1.0",
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age,
    )

    app.dependency_overrides[get_settings] = lambda: settings

    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @app.get("/", response_class=HTMLResponse)
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

    @app.get("/profile", response_class=HTMLResponse)
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

    @app.get("/auth/login")
    async def login(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> RedirectResponse:
        """Initiate OIDC login."""
        redirect_uri = f"{settings.base_url}/auth/callback"
        authorization_url = await oidc_client.get_authorization_url(request, redirect_uri)
        return RedirectResponse(url=authorization_url)

    @app.get("/auth/callback")
    async def callback(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> RedirectResponse:
        """Handle OIDC callback."""
        try:
            token, user = await oidc_client.handle_callback(request)
        except Exception as e:
            error_detail = f"OIDC callback failed: {type(e).__name__}: {e}"
            logger.exception("OIDC callback error")
            if settings.debug:
                raise HTTPException(status_code=500, detail=error_detail)
            raise HTTPException(
                status_code=500,
                detail="Authentication failed. Check server logs for details.",
            ) from e
        request.session["user"] = user.raw_claims
        request.session["access_token"] = token.get("access_token")
        return RedirectResponse(url="/")

    @app.get("/auth/logout")
    async def logout(request: Request) -> RedirectResponse:
        """Logout and clear session."""
        request.session.clear()
        return RedirectResponse(url="/")

    @app.get("/api/protected")
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

    @app.get("/health")
    async def health() -> dict:
        """Health check."""
        return {"status": "healthy"}

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy(request: Request):
        """Privacy policy page."""
        return templates.TemplateResponse(
            request=request,
            name="privacy.html",
            context={"user": None},
        )

    @app.get("/aup", response_class=HTMLResponse)
    async def aup(request: Request):
        """Acceptable use policy page."""
        return templates.TemplateResponse(
            request=request,
            name="aup.html",
            context={"user": None},
        )

    return app


def get_demo_app() -> FastAPI:
    """Get demo application instance."""
    return create_demo_app()


if __name__ == "__main__":
    import uvicorn

    app = create_demo_app()
    uvicorn.run(app, host="0.0.0.0", port=8124)
