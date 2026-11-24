"""FastAPI application with SRAM OIDC authentication."""

from typing import Annotated

from fastapi import Depends, FastAPI

from sram_fastapi.auth import (
    User,
    get_current_user,
    get_optional_user,
    get_token_user,
)
from sram_fastapi.auth_routes import add_session_middleware, create_auth_router
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

    add_session_middleware(app, settings)

    # Override settings dependency for testing
    app.dependency_overrides[get_settings] = lambda: settings

    app.include_router(create_auth_router())
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
                "message": f"Hello, {user.name or user.email or user.identifier}!",
                "authenticated": True,
            }
        return {
            "message": "Welcome! Please login to continue.",
            "authenticated": False,
            "login_url": "/auth/login",
        }

    @app.get("/auth/me")
    async def me(
        user: Annotated[User, Depends(get_current_user)],
    ) -> dict:
        """Get current user information (requires authentication)."""
        return {
            "identifier": user.identifier,
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
            "user": user.identifier,
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
