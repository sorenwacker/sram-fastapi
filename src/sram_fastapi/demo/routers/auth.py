"""Authentication routes for the demo application."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from sram_fastapi.auth import OIDCClient, get_oidc_client
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Browsers drop cookies over 4096 bytes; the signed, base64-encoded session cookie is
# about a third larger than its JSON content
SESSION_JSON_WARNING_BYTES = 2800


def warn_if_session_too_large(session: dict[str, Any]) -> None:
    """Log the session's field sizes when its cookie is likely to be dropped.

    Only key names and sizes are logged, never values.

    Args:
        session: The session content about to be stored in the cookie.
    """
    total = len(json.dumps(session, default=str))
    if total < SESSION_JSON_WARNING_BYTES:
        return
    sizes: dict[str, int] = {}
    for key, value in session.items():
        if isinstance(value, dict):
            for claim, claim_value in value.items():
                sizes[f"{key}.{claim}"] = len(json.dumps(claim_value, default=str))
        else:
            sizes[key] = len(json.dumps(value, default=str))
    largest = sorted(sizes.items(), key=lambda item: item[1], reverse=True)[:8]
    logger.warning(
        "Session is %d bytes of JSON; the browser may drop the cookie. Largest fields: %s",
        total,
        ", ".join(f"{name}={size}" for name, size in largest),
    )


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
            _, user = await oidc_client.handle_callback(request)
        except Exception as e:
            error_detail = f"OIDC callback failed: {type(e).__name__}: {e}"
            logger.exception("OIDC callback error")
            if settings.debug:
                raise HTTPException(status_code=500, detail=error_detail)
            raise HTTPException(
                status_code=500,
                detail="Authentication failed. Check server logs for details.",
            ) from e
        # Only the claims go in the session cookie; EngineBlock access tokens alone
        # exceed the 4 KB browsers accept for a cookie
        request.session["user"] = user.raw_claims
        warn_if_session_too_large(dict(request.session))
        return RedirectResponse(url="/")

    @router.get("/logout")
    async def logout(request: Request) -> RedirectResponse:
        """Logout and clear session."""
        request.session.clear()
        return RedirectResponse(url="/")

    return router
