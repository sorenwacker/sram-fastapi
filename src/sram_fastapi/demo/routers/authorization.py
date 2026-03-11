"""Authorization demo routes."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import User, get_optional_user, require_affiliation, require_entitlement

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Demo authorization requirements
DEMO_REQUIRED_ENTITLEMENT = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemogroup"
DEMO_REQUIRED_AFFILIATION = "member@"


def create_authorization_router() -> APIRouter:
    """Create authorization demo router."""
    router = APIRouter(prefix="/demo", tags=["authorization"])
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @router.get("/authorization", response_class=HTMLResponse)
    async def authorization_demo(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
    ):
        """Authorization demo landing page."""
        return templates.TemplateResponse(
            request=request,
            name="authorization.html",
            context={
                "user": user,
                "required_entitlement": DEMO_REQUIRED_ENTITLEMENT,
                "required_affiliation": DEMO_REQUIRED_AFFILIATION,
            },
        )

    @router.get("/entitlement-protected", response_class=HTMLResponse)
    async def entitlement_protected(
        request: Request,
        user: Annotated[User, Depends(require_entitlement(DEMO_REQUIRED_ENTITLEMENT))],
    ):
        """Page protected by entitlement requirement."""
        return templates.TemplateResponse(
            request=request,
            name="entitlement_protected.html",
            context={
                "user": user,
                "required_entitlement": DEMO_REQUIRED_ENTITLEMENT,
            },
        )

    @router.get("/affiliation-protected", response_class=HTMLResponse)
    async def affiliation_protected(
        request: Request,
        user: Annotated[User, Depends(require_affiliation(DEMO_REQUIRED_AFFILIATION))],
    ):
        """Page protected by affiliation requirement."""
        return templates.TemplateResponse(
            request=request,
            name="affiliation_protected.html",
            context={
                "user": user,
                "required_affiliation": DEMO_REQUIRED_AFFILIATION,
            },
        )

    return router
