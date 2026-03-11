"""FastAPI application with SRAM OIDC authentication."""

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI application."""
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="FastAPI application with SRAM OIDC authentication",
        version="0.1.0",
    )

    # Add session middleware for OAuth state
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age,
        https_only=settings.session_https_only,
        same_site="lax",
    )

    # Override settings dependency for testing
    app.dependency_overrides[get_settings] = lambda: settings

    register_routes(app)

    return app


def register_routes(app: FastAPI) -> None:
    """Register application routes."""

    @app.get("/")
    async def root(
        user: Annotated[User | None, Depends(get_optional_user)],
    ) -> dict:
        """Public endpoint showing authentication status."""
        if user:
            return {
                "message": f"Hello, {user.name or user.email or user.sub}!",
                "authenticated": True,
            }
        return {
            "message": "Welcome! Please login to continue.",
            "authenticated": False,
            "login_url": "/auth/login",
        }

    @app.get("/auth/login")
    async def login(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> RedirectResponse:
        """Initiate OIDC login flow."""
        redirect_uri = f"{settings.base_url}/auth/callback"
        authorization_url = await oidc_client.get_authorization_url(request, redirect_uri)
        return RedirectResponse(url=authorization_url)

    @app.get("/auth/callback")
    async def callback(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
    ) -> RedirectResponse:
        """Handle OIDC callback."""
        token, user = await oidc_client.handle_callback(request)

        # Store user info in session
        request.session["user"] = user.raw_claims
        request.session["access_token"] = token.get("access_token")

        return RedirectResponse(url="/")

    @app.get("/auth/logout")
    async def logout(request: Request) -> RedirectResponse:
        """Logout and clear session."""
        request.session.clear()
        return RedirectResponse(url="/")

    @app.get("/auth/me")
    async def me(
        user: Annotated[User, Depends(get_current_user)],
    ) -> dict:
        """Get current user information (requires authentication)."""
        return {
            "sub": user.sub,
            "email": user.email,
            "name": user.name,
            "preferred_username": user.preferred_username,
            "eduperson_entitlement": user.eduperson_entitlement,
        }

    @app.get("/api/protected")
    async def protected_api(
        user: Annotated[User, Depends(get_token_user)],
    ) -> dict:
        """Protected API endpoint (requires Bearer token)."""
        return {
            "message": "You have access to the protected API",
            "user": user.sub,
        }

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "healthy"}


def get_app() -> FastAPI:
    """Get or create the application instance."""
    return create_app()


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8124)
