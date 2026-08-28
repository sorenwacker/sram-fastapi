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

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import AuthorizationError, User, get_optional_user
from sram_fastapi.collaborations import (
    Collaboration,
    CollaborationCreate,
    CollaborationNotFoundError,
    Role,
    SRAMAPIError,
    SRAMOrganisationClient,
    collaboration_urns,
    entitlement_for,
    get_organisation_client,
    identifier_of,
)
from sram_fastapi.config import Settings, get_settings

logger = logging.getLogger(__name__)

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

    The role is read from the collaboration's own membership list. The OIDC subject and
    the uid the organisation API returns describe the same person with different hosts,
    so the comparison is on the identifier itself rather than on the whole string.
    """
    subject = identifier_of(user.sub)
    if not subject:
        return False
    return any(identifier_of(uid) == subject for uid in collaboration.admin_uids())


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


async def _managed_collaboration(
    identifier: str,
    user: User,
    client: SRAMOrganisationClient,
    settings: Settings,
) -> Collaboration:
    """Load a collaboration and check that the user may manage its membership.

    Args:
        identifier: The collaboration's SRAM identifier.
        user: The logged-in user.
        client: The organisation API client.
        settings: Application settings.

    Returns:
        The collaboration.

    Raises:
        AuthorizationError: If the user is neither an admin of the collaboration
            nor a holder of the manager entitlement.
    """
    collaboration = await client.get_collaboration(identifier)
    if _is_manager(user, settings) or _is_collaboration_admin(user, collaboration):
        return collaboration
    raise AuthorizationError(
        required=[f"admin of {collaboration.global_urn or identifier}"],
        actual=user.eduperson_entitlement or [],
        check_type="entitlement",
    )


def _epoch_seconds(value: str | None) -> int | None:
    """Convert a date from a form field into epoch seconds.

    Args:
        value: A date in ISO format, or an empty value.

    Returns:
        The date as epoch seconds at midnight UTC, or None when no date was given.

    Raises:
        HTTPException: If the value is not a date SRAM can use.
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"'{value}' is not a date in YYYY-MM-DD format."
        ) from exc
    return int(parsed.timestamp())


def _own_group(collaboration: Collaboration, group_identifier: str) -> None:
    """Check that a group belongs to the collaboration the caller was authorized for.

    Authorization is granted per collaboration, but group endpoints address a group
    directly, so without this check an admin of one collaboration could act on the
    groups of another.

    Args:
        collaboration: The collaboration the caller may manage.
        group_identifier: The group the request wants to change.

    Raises:
        CollaborationNotFoundError: If the group is not part of the collaboration.
    """
    if group_identifier not in {group.identifier for group in collaboration.groups}:
        raise CollaborationNotFoundError(
            f"Group {group_identifier} is not part of this collaboration."
        )


async def _own_invitation(
    identifier: str, invitation_id: str, client: SRAMOrganisationClient
) -> None:
    """Check that an invitation is open in the collaboration the caller was authorized for.

    Args:
        identifier: The collaboration's SRAM identifier.
        invitation_id: The invitation the request wants to change.
        client: The organisation API client.

    Raises:
        CollaborationNotFoundError: If the invitation is not open in the collaboration.
    """
    invitations = await client.list_open_invitations(identifier)
    if invitation_id not in {invitation.identifier for invitation in invitations}:
        raise CollaborationNotFoundError(
            f"Invitation {invitation_id} is not open in this collaboration."
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
            return RedirectResponse("/auth/login", status_code=303)

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

        if client.configured:
            organisation = await client.get_organisation()
            context["collaborations"] = [
                c for c in organisation.collaborations if c.global_urn in urns
            ]
            if is_manager:
                context["organisation"] = organisation

        return templates.TemplateResponse(
            request=request,
            name="collaborations.html",
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
            return RedirectResponse("/auth/login", status_code=303)
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
        expiry_date: Annotated[str, Form()] = "",
        disable_join_requests: Annotated[bool, Form()] = False,
        disclose_member_information: Annotated[bool, Form()] = False,
        disclose_email_information: Annotated[bool, Form()] = False,
    ) -> Response:
        """Create a collaboration and connect this service to it."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        _require_manager(user, settings)

        if not settings.sram_service_entity_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "SRAM_SERVICE_ENTITY_ID is not configured, so a new collaboration "
                    "could not be connected to this service."
                ),
            )

        expiry = _epoch_seconds(expiry_date)
        spec = CollaborationCreate(
            name=name,
            description=description,
            administrators=_split_list(administrators),
            short_name=short_name or None,
            website_url=website_url or None,
            message=message or None,
            tags=_split_list(tags),
            units=_split_list(units),
            expiry_date=expiry,
            disable_join_requests=disable_join_requests,
            disclose_member_information=disclose_member_information,
            disclose_email_information=disclose_email_information,
        )

        collaboration = await client.create_collaboration(spec)
        try:
            await client.connect_service(collaboration.identifier)
        except SRAMAPIError:
            logger.error(
                "Could not connect this service to the new collaboration %s; "
                "removing it again so no unreachable collaboration is left behind.",
                collaboration.identifier,
            )
            await client.delete_collaboration(collaboration.identifier)
            raise

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
            return RedirectResponse("/auth/login", status_code=303)
        _require_manager(user, settings)

        if not settings.collaboration_deletion_enabled:
            raise AuthorizationError(
                required=["COLLABORATION_DELETION_ENABLED"],
                actual=[],
                check_type="setting",
            )

        await client.delete_collaboration(identifier)
        return RedirectResponse("/collaborations", status_code=303)

    @router.post("/{identifier}/invite")
    async def invite_members(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        emails: Annotated[str, Form()],
        role: Annotated[Role, Form()] = "member",
        message: Annotated[str, Form()] = "",
    ) -> Response:
        """Invite users to the collaboration by email."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)

        await client.invite(
            identifier,
            emails=_split_list(emails),
            role=role,
            message=message or None,
        )
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/members/role")
    async def change_member_role(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        uid: Annotated[str, Form()],
        role: Annotated[Role, Form()],
    ) -> Response:
        """Promote a member to admin, or demote an admin to member."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)

        await client.set_member_role(identifier, uid, role)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/members/remove")
    async def remove_member(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        uid: Annotated[str, Form()],
    ) -> Response:
        """Remove a member from the collaboration."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)

        await client.remove_member(identifier, uid)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/invitations/{invitation_id}/resend")
    async def resend_invitation(
        identifier: str,
        invitation_id: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Send an open invitation again."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)
        await _own_invitation(identifier, invitation_id, client)

        await client.resend_invitation(invitation_id)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/invitations/{invitation_id}/role")
    async def change_invitation_role(
        identifier: str,
        invitation_id: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        role: Annotated[Role, Form()],
    ) -> Response:
        """Change the role an invitee will get on acceptance."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)
        await _own_invitation(identifier, invitation_id, client)

        await client.update_invitation(invitation_id, role=role)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/invitations/{invitation_id}/withdraw")
    async def withdraw_invitation(
        identifier: str,
        invitation_id: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Withdraw an open invitation."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)
        await _own_invitation(identifier, invitation_id, client)

        await client.withdraw_invitation(invitation_id)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/groups")
    async def create_group(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        name: Annotated[str, Form()],
        short_name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        auto_provision_members: Annotated[bool, Form()] = False,
    ) -> Response:
        """Create a group inside the collaboration."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)

        await client.create_group(
            identifier,
            name=name,
            short_name=short_name,
            description=description or None,
            auto_provision_members=auto_provision_members,
        )
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/groups/{group_identifier}/update")
    async def update_group(
        identifier: str,
        group_identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        name: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
    ) -> Response:
        """Change the name or description of a group."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        collaboration = await _managed_collaboration(identifier, user, client, settings)
        _own_group(collaboration, group_identifier)

        await client.update_group(
            group_identifier, name=name or None, description=description or None
        )
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/groups/{group_identifier}/delete")
    async def delete_group(
        identifier: str,
        group_identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Delete a group."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        collaboration = await _managed_collaboration(identifier, user, client, settings)
        _own_group(collaboration, group_identifier)

        await client.delete_group(group_identifier)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/groups/{group_identifier}/members")
    async def add_group_member(
        identifier: str,
        group_identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        uid: Annotated[str, Form()],
    ) -> Response:
        """Add a collaboration member to a group."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        collaboration = await _managed_collaboration(identifier, user, client, settings)
        _own_group(collaboration, group_identifier)

        await client.add_group_member(group_identifier, uid)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/groups/{group_identifier}/members/remove")
    async def remove_group_member(
        identifier: str,
        group_identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
        uid: Annotated[str, Form()],
    ) -> Response:
        """Remove a member from a group."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        collaboration = await _managed_collaboration(identifier, user, client, settings)
        _own_group(collaboration, group_identifier)

        await client.remove_group_member(group_identifier, uid)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/services/connect")
    async def connect_service(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Connect this service to the collaboration."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)

        await client.connect_service(identifier)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

    @router.post("/{identifier}/services/disconnect")
    async def disconnect_service(
        identifier: str,
        user: Annotated[User | None, Depends(get_optional_user)],
        client: Annotated[SRAMOrganisationClient, Depends(get_organisation_client)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Disconnect this service from the collaboration."""
        if user is None:
            return RedirectResponse("/auth/login", status_code=303)
        await _managed_collaboration(identifier, user, client, settings)

        await client.disconnect_service(identifier)
        return RedirectResponse(f"/collaborations/{identifier}", status_code=303)

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
            return RedirectResponse("/auth/login", status_code=303)

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

        collaboration = await client.get_collaboration(identifier)

        _require_access(user, collaboration, settings)

        is_manager = _is_manager(user, settings)
        is_admin = _is_collaboration_admin(user, collaboration)
        can_manage = is_admin or is_manager
        invitations = await client.list_open_invitations(identifier) if can_manage else []
        return templates.TemplateResponse(
            request=request,
            name="collaboration_detail.html",
            context={
                "user": user,
                "collaboration": collaboration,
                "members": _member_views(collaboration, reveal=can_manage),
                "invitations": invitations,
                "is_admin": is_admin,
                "is_manager": is_manager,
                "deletion_enabled": settings.collaboration_deletion_enabled,
                "can_manage": can_manage,
                "hidden_label": HIDDEN,
            },
        )

    return router
