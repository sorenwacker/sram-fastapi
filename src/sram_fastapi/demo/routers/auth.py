"""Authentication routes for the demo application."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from sram_fastapi.auth import OIDCClient, get_oidc_client
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)


def create_auth_router() -> APIRouter:
    """Create authentication router."""
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/login")
    async def login(
        request: Request,
        oidc_client: Annotated[OIDCClient, Depends(get_oidc_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> RedirectResponse:
        """Initiate OIDC login."""
        redirect_uri = f"{settings.base_url}/auth/callback"
        return await oidc_client.authorize_redirect(request, redirect_uri)

    @router.get("/callback")
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

    @router.get("/logout")
    async def logout(request: Request) -> RedirectResponse:
        """Logout and clear session."""
        request.session.clear()
        return RedirectResponse(url="/")

    return router
