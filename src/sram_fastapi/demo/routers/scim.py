"""SCIM pull page for the demo application.

Lists every user and group SRAM provisions to this service. Because that includes
members of every connected collaboration, the page is limited to managers.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from sram_fastapi.auth import User, get_optional_user
from sram_fastapi.config import Settings, get_settings
from sram_fastapi.demo.routers.collaborations import require_manager
from sram_fastapi.demo.templating import create_templates
from sram_fastapi.scim import SCIMDirectory, SCIMGroup, SCIMUser, SRAMSCIMClient, get_scim_client
from sram_fastapi.scim_push import SCIMStore, get_scim_store


def create_scim_router() -> APIRouter:
    """Create the SCIM pull router.

    Returns:
        APIRouter serving the /scim page.
    """
    router = APIRouter(tags=["scim"])
    templates = create_templates()

    @router.get("/scim", response_class=HTMLResponse)
    async def scim_directory(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMSCIMClient, Depends(get_scim_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        store: Annotated[SCIMStore, Depends(get_scim_store)],
    ) -> Response:
        """Show the users and groups SRAM provisions through SCIM pull."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        require_manager(user, settings)

        directory = await client.get_directory() if client.configured else None
        return templates.TemplateResponse(
            request=request,
            name="scim.html",
            context={
                "user": user,
                "directory": directory,
                "pushed": SCIMDirectory(
                    users=[SCIMUser.from_resource(r) for r in store.list("Users")],
                    groups=[SCIMGroup.from_resource(r) for r in store.list("Groups")],
                ),
                "push_enabled": bool(settings.sram_scim_push_token),
            },
        )

    return router
