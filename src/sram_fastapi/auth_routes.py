"""Session middleware and the browser login routes shared by every application."""

import json
import logging
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from sram_fastapi.auth import OIDCClient, get_oidc_client
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

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


def add_session_middleware(app: FastAPI, settings: Settings) -> None:
    """Add the signed-cookie session and refuse cross-site state changes.

    Args:
        app: The application to configure.
        settings: Settings providing the key and cookie parameters.
    """
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age,
        https_only=settings.session_https_only,
        same_site="lax",
    )
    own_origin = _origin_of(settings.base_url)

    @app.middleware("http")
    async def refuse_cross_site_changes(request: Request, call_next):
        """Refuse state-changing requests that a browser sent from another origin."""
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin and _origin_of(origin) != own_origin:
            return JSONResponse(status_code=403, content={"detail": "Cross-site request refused"})
        return await call_next(request)


def _origin_of(url: str) -> str:
    """Return the scheme, host and port of a URL, the part an Origin header carries."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def create_auth_router() -> APIRouter:
    """Create the login, callback and logout routes under /auth.

    Returns:
        APIRouter for the browser login flow.
    """
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
