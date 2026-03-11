"""Demo application with HTML templates for SRAM authentication."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sram_fastapi.auth import AuthorizationError, get_optional_user
from sram_fastapi.config import Settings, get_settings
from sram_fastapi.demo.routers import (
    create_auth_router,
    create_authorization_router,
    create_pages_router,
)

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
        https_only=settings.session_https_only,
        same_site="lax",
    )

    app.dependency_overrides[get_settings] = lambda: settings

    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(
        request: Request, exc: AuthorizationError
    ) -> HTMLResponse:
        """Handle authorization errors with a user-friendly page."""
        return templates.TemplateResponse(
            request=request,
            name="forbidden.html",
            status_code=403,
            context={
                "user": get_optional_user(request),
                "required": exc.required,
                "actual": exc.actual,
                "check_type": exc.check_type,
                "require_all": exc.require_all,
            },
        )

    # Include routers
    app.include_router(create_pages_router())
    app.include_router(create_auth_router())
    app.include_router(create_authorization_router())

    return app


def get_demo_app() -> FastAPI:
    """Get demo application instance."""
    return create_demo_app()


if __name__ == "__main__":
    import uvicorn

    app = create_demo_app()
    uvicorn.run(app, host="0.0.0.0", port=8124)
