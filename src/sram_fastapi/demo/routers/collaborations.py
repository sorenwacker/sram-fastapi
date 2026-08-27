"""Collaboration management pages for the demo application.

These pages expose the SRAM organisation API: the collaborations a user belongs
to, and the members, groups and connected services of one collaboration.

Access rules:
    - Every page requires a logged-in user.
    - A collaboration may be opened by its members, identified through the
      ``eduperson_entitlement`` claim, and by holders of the manager entitlement.
    - Full member details are shown to collaboration admins and managers; other
      members see only what the collaboration's disclosure settings allow.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import AuthorizationError, User, get_optional_user
from sram_fastapi.collaborations import (
    Collaboration,
    CollaborationCreate,
    SRAMAPIError,
    SRAMOrganisationClient,
    collaboration_urns,
    entitlement_for,
    get_organisation_client,
)
from sram_fastapi.config import Settings, get_settings

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

HIDDEN = "not disclosed"


@dataclass
class MemberView:
    """A membership as shown on the detail page, with disclosure rules applied."""

    uid: str | None
    role: str
    status: str | None
    expiry_date: int | None
    name: str
    email: str


def _is_manager(user: User, settings: Settings) -> bool:
    """Whether the user holds the entitlement that allows managing collaborations."""
    required = settings.collaboration_manager_entitlement
    return bool(required) and required in (user.eduperson_entitlement or [])


def _is_member(user: User, collaboration: Collaboration) -> bool:
    """Whether the user's entitlements imply membership of the collaboration."""
    if not collaboration.global_urn:
        return False
    return collaboration.global_urn in collaboration_urns(user.eduperson_entitlement)


def _is_collaboration_admin(user: User, collaboration: Collaboration) -> bool:
    """Whether the user is an administrator of the collaboration.

    The role is read from the collaboration's own membership list, matching the
    OIDC subject against the SRAM uid.
    """
    return user.sub in collaboration.admin_uids()


def _member_views(collaboration: Collaboration, reveal: bool) -> list[MemberView]:
    """Apply the collaboration's disclosure settings to its membership list.

    Args:
        collaboration: The collaboration whose members are shown.
        reveal: True for viewers who may see every detail, such as admins.

    Returns:
        One view per membership, with undisclosed fields replaced.
    """
    show_names = reveal or bool(collaboration.disclose_member_information)
    show_emails = reveal or bool(collaboration.disclose_email_information)

    def value(shown: bool, actual: str | None) -> str:
        if not shown:
            return HIDDEN
        return actual or "unknown"

    return [
        MemberView(
            uid=membership.uid,
            role=membership.role,
            status=membership.status,
            expiry_date=membership.expiry_date,
            name=value(show_names, membership.name),
            email=value(show_emails, membership.email),
        )
        for membership in collaboration.memberships
    ]


def _require_access(user: User, collaboration: Collaboration, settings: Settings) -> None:
    """Raise unless the user may open the collaboration.

    Raises:
        AuthorizationError: If the user is neither a member nor a manager.
    """
    if _is_member(user, collaboration) or _is_manager(user, settings):
        return
    raise AuthorizationError(
        required=[entitlement_for(collaboration.global_urn or "")],
        actual=user.eduperson_entitlement or [],
        check_type="entitlement",
    )


def _require_manager(user: User, settings: Settings) -> None:
    """Raise unless the user may provision collaborations.

    Raises:
        AuthorizationError: If the user does not hold the manager entitlement.
    """
    if _is_manager(user, settings):
        return
    raise AuthorizationError(
        required=[settings.collaboration_manager_entitlement or "collaboration manager"],
        actual=user.eduperson_entitlement or [],
        check_type="entitlement",
    )


def _split_list(value: str | None) -> list[str]:
    """Split a comma or newline separated form field into a list of values."""
    if not value:
        return []
    parts = value.replace("\n", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


def create_collaborations_router() -> APIRouter:
    """Create the collaboration management router.

    Returns:
        APIRouter serving the collaboration overview and detail pages.
    """
    router = APIRouter(prefix="/collaborations", tags=["collaborations"])
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @router.get("", response_class=HTMLResponse)
    async def list_collaborations(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Show the collaborations the user belongs to.

        Managers additionally see every collaboration of the organisation.
        """
        if user is None:
            return RedirectResponse("/auth/login")

        is_manager = _is_manager(user, settings)
        urns = collaboration_urns(user.eduperson_entitlement)
        context = {
            "user": user,
            "configured": client.configured,
            "is_manager": is_manager,
            "member_urns": sorted(urns),
            "collaborations": [],
            "organisation": None,
            "error": None,
        }

        status_code = 200
        if client.configured:
            try:
                organisation = await client.get_organisation()
                context["collaborations"] = [
                    c for c in organisation.collaborations if c.global_urn in urns
                ]
                if is_manager:
                    context["organisation"] = organisation
            except SRAMAPIError as exc:
                context["error"] = str(exc)
                status_code = 502

        return templates.TemplateResponse(
            request=request,
            name="collaborations.html",
            status_code=status_code,
            context=context,
        )

    @router.get("/new", response_class=HTMLResponse)
    async def new_collaboration_form(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Show the form for provisioning a collaboration."""
        if user is None:
            return RedirectResponse("/auth/login")
        _require_manager(user, settings)

        return templates.TemplateResponse(
            request=request,
            name="collaboration_new.html",
            context={
                "user": user,
                "configured": client.configured,
                "service_entity_id": settings.sram_service_entity_id,
                "error": None,
            },
        )

    @router.post("/new", response_class=HTMLResponse)
    async def create_collaboration(
        request: Request,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        name: Annotated[str, Form()],
        description: Annotated[str, Form()],
        administrators: Annotated[str, Form()],
        short_name: Annotated[str, Form()] = "",
        website_url: Annotated[str, Form()] = "",
        message: Annotated[str, Form()] = "",
        tags: Annotated[str, Form()] = "",
        units: Annotated[str, Form()] = "",
        disable_join_requests: Annotated[bool, Form()] = False,
        disclose_member_information: Annotated[bool, Form()] = False,
        disclose_email_information: Annotated[bool, Form()] = False,
    ) -> Response:
        """Create a collaboration and connect this service to it."""
        if user is None:
            return RedirectResponse("/auth/login")
        _require_manager(user, settings)

        if not settings.sram_service_entity_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "SRAM_SERVICE_ENTITY_ID is not configured, so a new collaboration "
                    "could not be connected to this service."
                ),
            )

        spec = CollaborationCreate(
            name=name,
            description=description,
            administrators=_split_list(administrators),
            short_name=short_name or None,
            website_url=website_url or None,
            message=message or None,
            tags=_split_list(tags),
            units=_split_list(units),
            disable_join_requests=disable_join_requests,
            disclose_member_information=disclose_member_information,
            disclose_email_information=disclose_email_information,
        )

        try:
            collaboration = await client.create_collaboration(spec)
            await client.connect_service(collaboration.identifier)
        except SRAMAPIError as exc:
            return templates.TemplateResponse(
                request=request,
                name="collaboration_new.html",
                status_code=502,
                context={
                    "user": user,
                    "configured": client.configured,
                    "service_entity_id": settings.sram_service_entity_id,
                    "error": str(exc),
                },
            )

        return RedirectResponse(f"/collaborations/{collaboration.identifier}", status_code=303)

    @router.post("/{identifier}/delete")
    async def delete_collaboration(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Delete a collaboration and return to the overview."""
        if user is None:
            return RedirectResponse("/auth/login")
        _require_manager(user, settings)

        await client.delete_collaboration(identifier)
        return RedirectResponse("/collaborations", status_code=303)

    @router.get("/{identifier}", response_class=HTMLResponse)
    async def collaboration_detail(
        request: Request,
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Show one collaboration with its members, groups and connected services."""
        if user is None:
            return RedirectResponse("/auth/login")

        if not client.configured:
            return templates.TemplateResponse(
                request=request,
                name="collaborations.html",
                context={
                    "user": user,
                    "configured": False,
                    "is_manager": _is_manager(user, settings),
                    "member_urns": sorted(collaboration_urns(user.eduperson_entitlement)),
                    "collaborations": [],
                    "organisation": None,
                    "error": None,
                },
            )

        try:
            collaboration = await client.get_collaboration(identifier)
        except SRAMAPIError as exc:
            return templates.TemplateResponse(
                request=request,
                name="collaborations.html",
                status_code=502,
                context={
                    "user": user,
                    "configured": True,
                    "is_manager": _is_manager(user, settings),
                    "member_urns": sorted(collaboration_urns(user.eduperson_entitlement)),
                    "collaborations": [],
                    "organisation": None,
                    "error": str(exc),
                },
            )

        _require_access(user, collaboration, settings)

        is_manager = _is_manager(user, settings)
        is_admin = _is_collaboration_admin(user, collaboration)
        return templates.TemplateResponse(
            request=request,
            name="collaboration_detail.html",
            context={
                "user": user,
                "collaboration": collaboration,
                "members": _member_views(collaboration, reveal=is_admin or is_manager),
                "is_admin": is_admin,
                "is_manager": is_manager,
                "can_manage": is_admin or is_manager,
                "hidden_label": HIDDEN,
            },
        )

    return router
