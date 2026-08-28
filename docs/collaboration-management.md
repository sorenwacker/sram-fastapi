# Collaboration Management

How the application provisions collaborations in SRAM, invites users, manages admins and members, and shows a collaboration's member list.

## Scope

SRAM exposes a public organisation API that supports creating collaborations, inviting users, changing roles, removing members, and reading a collaboration with its full membership list. The application uses that API to offer collaboration management to users who are authorised for it, instead of sending them to the SRAM portal.

Two SRAM credentials are involved, and they are unrelated to the OIDC client credentials used for login:

| Credential | Issued by | Used for |
|------------|-----------|----------|
| Organisation API token | An organisation admin or manager, on the organisation's **API tokens** tab in SRAM | All write operations and reading collaboration details, scoped to one organisation |
| Service token (SCIM client) | SURF, per registered service | Read-only `/api/scim/v2/Users` and `/api/scim/v2/Groups` for collaborations connected to this service |

The organisation API token is the credential this feature is built on. The SCIM route is read-only and is not used here.

## What the organisation API supports

All endpoints are on `https://sram.surf.nl` and authenticate with `Authorization: bearer <organisation-api-token>`.

| Operation | Method and path |
|-----------|-----------------|
| List the organisation with all collaborations, groups, memberships, units, services and tags | `GET /api/organisations/v1` |
| Create a collaboration | `POST /api/collaborations/v1` |
| Read a collaboration, including `collaboration_memberships`, `groups` and `services` | `GET /api/collaborations/v1/{co_identifier}` |
| Delete a collaboration | `DELETE /api/collaborations/v1/{co_identifier}` |
| Set a user's role to `admin` or `member` | `PUT /api/collaborations/v1/{co_identifier}/members` |
| Remove a member | `DELETE /api/collaborations/v1/{co_identifier}/members/{user_uid}` |
| Invite users by email, in bulk, with an intended role | `PUT /api/invitations/v1/collaboration_invites` |
| List open invitations of a collaboration | `GET /api/invitations/v1/invitations/{co_identifier}` |
| Resend, update or withdraw an invitation | `PUT /api/invitations/v1/resend/{external_identifier}`, `PATCH /api/invitations/v1/update/{external_identifier}`, `DELETE /api/invitations/v1/{external_identifier}` |
| Connect this service to a collaboration | `PUT /api/collaborations_services/v1/connect_collaboration_service/{co_identifier}` |
| Create, update or delete a group and its memberships | `POST`/`PUT`/`DELETE /api/groups/v1[/{group_identifier}]` |

### Two ways to add a user

The distinction determines what the user interface can offer.

**By email, through an invitation.** `PUT /api/invitations/v1/collaboration_invites` takes `invites` (email addresses), `intended_role` (`member` or `admin`), an optional `message`, `invitation_expiry_date` and `membership_expiry_date` in epoch milliseconds, and optional `groups` the invitee joins on acceptance. The collaboration is addressed by `collaboration_identifier` or `short_name`. SRAM sends the email; membership starts when the invitee accepts. The user does not need to exist in SRAM beforehand.

**By uid, immediately.** `PUT /api/collaborations/v1/{co_identifier}/members` takes `uid` and `role` and takes effect at once, with no email and no acceptance step. The uid is the SRAM user identifier, for example `7e28ebe36633f958e75a15a803aa6f4a7f0ab8ac@acc.sram.eduteams.org`. It is known only for users the application has already seen in a membership list, so this path is for changing the role of an existing member, not for adding strangers.

`POST /api/collaborations/v1` combines both at creation time: `administrators` is a list of email addresses that receive an invitation, and `administrator` is a single uid that becomes admin without an invitation.

### Creating a usable collaboration

A newly created collaboration is not connected to any service. Its members cannot log in to this application until the service is connected with `PUT /api/collaborations_services/v1/connect_collaboration_service/{co_identifier}`, whose body is `{"service_entity_id": "<entity id of this service>"}`. Provisioning is therefore two calls, and the application performs both.

`POST /api/collaborations/v1` requires `name`, `description`, `disable_join_requests`, `disclose_member_information`, `disclose_email_information` and `administrators`. `short_name` is generated when omitted. The response carries the generated `identifier` (a UUID) and `global_urn`, both of which the application needs afterwards.

Because provisioning is two calls, it can fail halfway. If the collaboration is created but the connection fails, the application deletes the collaboration again and reports the failure, so no collaboration is left behind that nobody could reach. Deletion is safe at that point: the collaboration is seconds old, has no members beyond the pending admin invitations, and no service.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `SRAM_API_BASE_URL` | `https://sram.surf.nl` | Base URL of the SRAM API. Set to `https://acc.sram.surf.nl` for the acceptance environment |
| `SRAM_ORGANISATION_API_TOKEN` | unset | Organisation API token. Collaboration management is disabled while this is unset |
| `SRAM_SERVICE_ENTITY_ID` | unset | Entity ID of this service, used to connect it to a newly created collaboration. Provisioning is disabled while this is unset |
| `COLLABORATION_MANAGER_ENTITLEMENT` | unset | Entitlement a user must hold to provision collaborations. Provisioning is disabled while this is unset |
| `COLLABORATION_DELETION_ENABLED` | `false` | Whether the delete control is offered. Deletion destroys every membership and cannot be undone, so it is off unless a deployment asks for it |

Every one of these is optional, and each missing value disables the part of the feature that depends on it rather than causing an error at startup. This matches how `SRAM_INTROSPECTION_TOKEN` already behaves.

## Deployment posture

The organisation API token is an organisation-wide administrator credential: it can change and delete every collaboration in the organisation, not only the ones this application shows. That makes the application the only gate between a logged-in user and organisation-wide change, so the deployment defaults are chosen to keep the blast radius small.

`sram_api_base_url` defaults to the acceptance environment, `https://acc.sram.surf.nl`, in the deployment variables. Pointing a deployment at production is a deliberate decision, taken with a token issued for that purpose, ideally for an organisation that exists for this application rather than the institution's main one.

`COLLABORATION_DELETION_ENABLED` is false, so the delete control is not offered. The page says so and names the setting, rather than hiding the capability. The switch governs the route only: when provisioning cannot connect the service, the application still deletes the collaboration it just created, because leaving an unreachable collaboration behind would be worse.

Keep `COLLABORATION_MANAGER_ENTITLEMENT` pointed at a small group. Its holders can provision, and where deletion is enabled, delete.

If an application only needs to show who is in a collaboration, it does not need any of this: the service's own SCIM token gives read-only access to the users and groups of the collaborations connected to it.

## Python API

A new module `sram_fastapi.collaborations` holds the client. It is a library component: it takes its settings by injection and performs no authorization decisions of its own.

```python
from sram_fastapi.collaborations import SRAMOrganisationClient, CollaborationCreate

client = SRAMOrganisationClient(settings)

collaboration = await client.create_collaboration(
    CollaborationCreate(
        name="Cumulus research group",
        description="Cumulus research group of the University of Harderwijk.",
        administrators=["jdoe@uniharderwijk.nl"],
        disable_join_requests=True,
        disclose_member_information=True,
        disclose_email_information=False,
    )
)
await client.connect_service(collaboration.identifier)
```

Methods:

| Method | SRAM call |
|--------|-----------|
| `get_organisation()` | `GET /api/organisations/v1` |
| `create_collaboration(spec)` | `POST /api/collaborations/v1` |
| `get_collaboration(identifier)` | `GET /api/collaborations/v1/{id}` |
| `delete_collaboration(identifier)` | `DELETE /api/collaborations/v1/{id}` |
| `connect_service(identifier, service_entity_id=None)` | `PUT /api/collaborations_services/v1/connect_collaboration_service/{id}` |
| `list_members(identifier)` | `GET /api/collaborations/v1/{id}`, returning its memberships |
| `set_member_role(identifier, uid, role)` | `PUT /api/collaborations/v1/{id}/members` |
| `remove_member(identifier, uid)` | `DELETE /api/collaborations/v1/{id}/members/{uid}` |
| `disconnect_service(identifier, service_entity_id=None)` | `PUT /api/collaborations_services/v1/disconnect_collaboration_service/{id}` |
| `invite(identifier, emails, role, ...)` | `PUT /api/invitations/v1/collaboration_invites` |
| `list_open_invitations(identifier)` | `GET /api/invitations/v1/invitations/{id}` |
| `resend_invitation(external_identifier)` | `PUT /api/invitations/v1/resend/{external_identifier}` |
| `update_invitation(external_identifier, role=None, groups=None)` | `PATCH /api/invitations/v1/update/{external_identifier}` |
| `withdraw_invitation(external_identifier)` | `DELETE /api/invitations/v1/{external_identifier}` |
| `create_group(identifier, name, short_name, ...)` | `POST /api/groups/v1` |
| `update_group(group_identifier, ...)` | `PUT /api/groups/v1/{group_identifier}` |
| `delete_group(group_identifier)` | `DELETE /api/groups/v1/{group_identifier}` |
| `add_group_member(group_identifier, uid)` | `POST /api/groups/v1/{group_identifier}` |
| `remove_group_member(group_identifier, uid)` | `DELETE /api/groups/v1/{group_identifier}/members/{uid}` |

Dataclasses `Organisation`, `Collaboration`, `Membership`, `Group`, `Service`, `Invitation` and `CollaborationCreate` mirror the SRAM schemas and expose only the fields the application uses. `Membership` carries `uid`, `role`, `status`, `expiry_date`, `name` and `email`; the last two come from the nested `user` object.

### Errors

| Condition | Exception | Page |
|-----------|-----------|------|
| No organisation API token, or no service entity ID where one is required | `SRAMNotConfiguredError` | 503. Raised before any request is sent, so an unconfigured deployment never contacts SRAM |
| SRAM answers 401 or 403 | `OrganisationTokenError` | 502. The token is invalid, expired, or lacks rights on the target. Logged as an administrator action item, mirroring `IntrospectionTokenError` |
| SRAM answers 404 | `CollaborationNotFoundError` | 404. The collaboration, group or invitation does not exist, or is outside this organisation |
| SRAM answers 409 | `CollaborationConflictError` | 409. For example a duplicate group membership or short name |
| Transport failure, timeout, unexpected status, or a success response with no body | `SRAMAPIError` | 502. Reported as a SRAM failure, never as a failure of the user's data |

The demo application turns all of these into a page through one exception handler, so no SRAM failure reaches the user as an unhandled server error. Every handler states that nothing was changed, which holds because each route performs a single SRAM call. The one exception is provisioning, described below.

## Authorization in the application

The organisation API token is an organisation administrator credential that can also delete collaborations. It is held server-side only, is never derived from the session, and is never exposed to a browser. Every route that uses it enforces its own check first.

**Provisioning a collaboration** requires the entitlement named by `COLLABORATION_MANAGER_ENTITLEMENT`. A user without it gets 403 and the access denied page. If the setting is unset nobody holds it, the provisioning controls are not rendered, and an unconfigured deployment therefore cannot provision at all.

**Viewing a collaboration's members** requires that the requesting user is a member of that collaboration. Membership is read from the user's own `eduperson_entitlement` claim: a collaboration whose `global_urn` is `uniharderwijk:cumulusgrp` corresponds to the entitlement `urn:mace:surf.nl:sram:group:uniharderwijk:cumulusgrp`. The application matches on that value and never lists a collaboration the user does not belong to.

**Managing members and admins** requires either the manager entitlement, or admin role in the collaboration itself. SRAM does not publish an admin role in the entitlement claim, so the role is read from the collaboration's own membership list and the acting user is matched against it by SRAM uid.

!!! note "Matching a session to a membership"
    The two identifiers for one person differ by host. A real subject claim from the login proxy reads `8ba4f476f50522bdae80f78f60513bce3752afd4@sram.surf.nl`, while the organisation API returns uids ending in `@sram.eduteams.org`. Comparing the strings whole would therefore never match, so the comparison uses the part before the host, which the two share. `sub` is the only identifier the proxy sends: the token payload carries no separate uid claim.

    Holding `COLLABORATION_MANAGER_ENTITLEMENT` remains an independent path to the same routes. Both are enforced and tested, and neither opens access to a user who has neither.

### Keeping an authorized request inside its collaboration

Authorization is granted per collaboration, but SRAM's group and invitation endpoints address their object directly, and the organisation API token is valid for every collaboration in the organisation. Two rules keep the two in step.

A group or invitation named in a request must belong to the collaboration the caller was authorized for. The group is checked against the collaboration's own group list, the invitation against its open invitations; anything else is reported as not found, which also avoids confirming that the object exists elsewhere.

Every value placed in a SRAM API path is percent-encoded, and a value consisting only of dots is refused outright. Identifiers and uids arrive from URL paths and form fields, and an unencoded `/` or `..` would otherwise let a request resolve to a different collaboration than the one that was authorized.

## Privacy

A collaboration carries `disclose_member_information` and `disclose_email_information`. These govern what SRAM shows members in its own portal. They are **not** applied to the organisation API response: the token sees every member and every email address regardless of the flags.

The application therefore enforces them itself when rendering. For a user who is a member but not an admin of the collaboration, names are hidden unless `disclose_member_information` is true, and email addresses are hidden unless `disclose_email_information` is true. Admins of the collaboration see the full list. The flags are shown on the member list page so it is clear which rule is in force.

## User interface

Every capability described on this page is reachable from the demo application. Nothing is API-only.

| Page | Content |
|------|---------|
| `/collaborations` | The collaborations the logged-in user belongs to, derived from the entitlement claim and enriched from SRAM. Holders of the manager entitlement additionally see every collaboration of the organisation, read from `GET /api/organisations/v1`, and a "New collaboration" action |
| `/collaborations/new` | Form for name, short name, description, website URL, join requests, the two disclosure flags, expiry date, tags, units, administrator email addresses and the invitation message. On submit the application creates the collaboration, connects this service to it, and redirects to the detail page |
| `/collaborations/{identifier}` | Collaboration details and four tables: members, open invitations, groups, connected services. Each table carries the actions listed below |

Actions on the detail page:

| Table | Actions |
|-------|---------|
| Members | Invite by email with intended role, message and expiry dates; promote to admin; demote to member; remove member |
| Invitations | Resend; change intended role; withdraw. SRAM's invitation update accepts only the role and the target groups, so an invitation's expiry cannot be changed after it is sent |
| Groups | Create group; rename or re-describe a group; delete group; add a member to a group; remove a member from a group |
| Services | Connect this service; disconnect this service |
| Collaboration | Delete collaboration, where `COLLABORATION_DELETION_ENABLED` allows it |

Every action is a form post, so no action can be triggered by following a link:

| Route | Action |
|-------|--------|
| `POST /collaborations/new` | Create a collaboration and connect this service |
| `POST /collaborations/{id}/delete` | Delete the collaboration |
| `POST /collaborations/{id}/invite` | Invite email addresses with an intended role |
| `POST /collaborations/{id}/members/role` | Promote or demote a member |
| `POST /collaborations/{id}/members/remove` | Remove a member |
| `POST /collaborations/{id}/invitations/{invitation_id}/resend` | Resend an invitation |
| `POST /collaborations/{id}/invitations/{invitation_id}/role` | Change the intended role of an invitation |
| `POST /collaborations/{id}/invitations/{invitation_id}/withdraw` | Withdraw an invitation |
| `POST /collaborations/{id}/groups` | Create a group |
| `POST /collaborations/{id}/groups/{group_id}/update` | Rename or re-describe a group |
| `POST /collaborations/{id}/groups/{group_id}/delete` | Delete a group |
| `POST /collaborations/{id}/groups/{group_id}/members` | Add a member to a group |
| `POST /collaborations/{id}/groups/{group_id}/members/remove` | Remove a member from a group |
| `POST /collaborations/{id}/services/connect` | Connect this service |
| `POST /collaborations/{id}/services/disconnect` | Disconnect this service |

Pending invitations and the management controls are shown to collaboration admins and managers only; an ordinary member sees the member, group and service tables without them.

Each action states the SRAM call it performs, as method and path, next to the control. The application is a reference implementation, so showing which endpoint backs each control is part of what it demonstrates.

Removing a member, deleting a group, disconnecting a service and deleting a collaboration require an explicit confirmation step. The confirmation text is carried in a `data-confirm` attribute and read by one script that binds a submit handler: a browser decodes an attribute value before its content would reach the JavaScript parser, so a name or email interpolated into an inline `onsubmit` handler could break out of the string literal despite HTML escaping. Deleting a collaboration is offered only to holders of the manager entitlement, and only where the deployment enables it.

Two different reasons hide a control, and they read differently on the page. Where a **credential is missing**, the section is replaced by a note naming the environment variable to set, so the demo makes the credential requirements visible. Where the **user lacks the authority**, the control is simply absent, because naming a capability the viewer cannot have adds nothing.

## Testing

Tests use `pytest` with `httpx` transports mocked at the client boundary, so no test contacts SRAM. Each client method is tested for its request shape, its success parsing, and its error mapping for 401, 403, 404, 409 and transport failure.

Route tests cover the authorization matrix explicitly: anonymous, authenticated non-member, member, collaboration admin, and manager-entitlement holder, against every route. The privacy rules get their own tests, one per disclosure flag and role combination.

Behaviour when configuration is absent is covered as well: with `SRAM_ORGANISATION_API_TOKEN` unset, the pages report the feature as unconfigured rather than raising, and with `SRAM_SERVICE_ENTITY_ID` unset, provisioning is refused rather than leaving an unreachable collaboration behind.

The identity matching described above is the one thing tests cannot settle, since it depends on what SRAM issues. It is verified manually once in the acceptance environment at `https://acc.sram.surf.nl`, and the outcome is recorded here.

## Limitations

- The organisation API token is scoped to one organisation. The application can only create and manage collaborations under that organisation.
- Adding an existing user without an email invitation requires their SRAM uid, which is known only for users already present in a membership list.
- Invited users become members only after they accept. Until then they appear as open invitations, not as members.
- SRAM has no webhook or event feed for membership changes. The application reads the current state on each request and holds no local copy of the membership.

## References

- [SRAM API specification](https://sram.surf.nl/apidocs/)
- [Use the organisation API](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226072/Use+the+organisation+API)
- [Invite admins and members to a collaboration](https://wiki.surfnet.nl/display/SRAM/Invite+admins+and+members+to+a+collaboration)
- [SRAM Setup](sram-setup.md) for registering the service and obtaining credentials
- [Authorization](authorization.md) for the entitlement and affiliation checks used here
