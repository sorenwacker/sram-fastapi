# SCIM Provisioning

How the application obtains SRAM users and groups through SCIM, independently of user logins.

## Background

SCIM (System for Cross-domain Identity Management, [RFC 7643](https://www.rfc-editor.org/rfc/rfc7643) and [RFC 7644](https://www.rfc-editor.org/rfc/rfc7644)) is a REST protocol for exchanging users and groups between systems. OIDC informs the application about a user only at the moment that user logs in. SCIM gives the application the full membership of the collaborations connected to it, including members who never logged in, and reflects removals.

SRAM offers SCIM in two directions ([SURF: Connect to SCIM](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226086/Connect+to+SCIM)):

| Mode | Direction | Authentication |
|------|-----------|----------------|
| SCIM pull by application | The application requests `/api/scim/v2/Users` and `/api/scim/v2/Groups` from SRAM | Bearer token issued by SRAM |
| SCIM push to application | SRAM sends changes to SCIM endpoints served by the application | Bearer token issued by the application |

This application implements both modes as a proof of concept. Pulled data is read from SRAM on each request; pushed data is held in memory.

## SCIM pull

### Setup in SRAM

1. Open the service in SRAM and go to **Details & settings**.
2. In the section **SCIM pull by application**, enable **SCIM pull by application**.
3. Press **New token** and copy the token into `SRAM_SCIM_TOKEN`.

```bash
SRAM_SCIM_TOKEN=token-from-sram
SRAM_API_BASE_URL=https://sram.surf.nl   # https://acc.sram.surf.nl for acceptance
```

The token is a service credential. It is held server-side only and is never sent to a browser.

### Endpoints

| Endpoint | Returns |
|----------|---------|
| `GET {SRAM_API_BASE_URL}/api/scim/v2/Users` | All users of all collaborations connected to the service |
| `GET {SRAM_API_BASE_URL}/api/scim/v2/Groups` | All collaborations and groups connected to the service, with their members |
| `GET {SRAM_API_BASE_URL}/api/scim/v2/Schemas` | Schema definitions; no authentication required |

Responses are SCIM `ListResponse` objects. The application requests further pages with `startIndex` until it has `totalResults` resources, since a service provider may return fewer resources per response than exist. Besides the core schemas, SRAM adds two extensions:

| Extension | Attributes |
|-----------|-----------|
| `urn:mace:surf.nl:sram:scim:extension:User` | `eduPersonScopedAffiliation`, `eduPersonUniqueId`, `voPersonExternalAffiliation`, `voPersonExternalId`, `sramInactiveDays` |
| `urn:mace:surf.nl:sram:scim:extension:Group` | `description`, `urn`, `labels` |

### In the application

The page `/scim` lists the users (user name, display name, email) and the groups (display name, URN, and the user names of their members). Group members are resolved against the user list by SCIM `id`.

Access requires a logged-in user holding the entitlement configured in `COLLABORATION_MANAGER_ENTITLEMENT`, because the page shows every member of every connected collaboration. Other users get 403.

| Situation | Result |
|-----------|--------|
| `SRAM_SCIM_TOKEN` not set | The page states that SCIM pull is not configured; no request is made to SRAM |
| SRAM answers 401 | The page reports that SRAM rejected the token |
| SRAM unreachable or answers 5xx | The page reports that SRAM could not be reached |

## SCIM push

### Setup

1. Generate a token, for example with `python -c "import secrets; print(secrets.token_urlsafe(32))"`, and set it as `SRAM_SCIM_PUSH_TOKEN`.
2. Open the service in SRAM and go to **Details & settings**.
3. In the section **SCIM push to application**, enable **SCIM push to application provisioning**.
4. Enter the base URL `{BASE_URL}/scim/v2`, for example `https://sram-demo.ewi.tudelft.nl/scim/v2`.
5. Enter the same token.
6. Optionally enable SCIM sweeping, a periodic full push that also repopulates the store after a restart.

SRAM sends push requests from the IP addresses SURF documents. The application authenticates them by token only.

### Endpoints served

| Method and path | Effect |
|-----------------|--------|
| `GET /scim/v2/Users`, `GET /scim/v2/Groups` | `ListResponse` of stored resources; supports `filter=externalId eq "<value>"`, which SRAM uses to find an existing resource |
| `GET /scim/v2/{Users,Groups}/{id}` | One stored resource, or 404 |
| `POST /scim/v2/{Users,Groups}` | Stores the resource under a new `id` and returns it with 201 |
| `PUT /scim/v2/{Users,Groups}/{id}` | Replaces the stored resource, or 404 |
| `DELETE /scim/v2/{Users,Groups}/{id}` | Removes the resource and returns 204, or 404 |

Every request needs `Authorization: Bearer <SRAM_SCIM_PUSH_TOKEN>`. A missing or wrong token gives 401. When `SRAM_SCIM_PUSH_TOKEN` is not set, all endpoints answer 404, so push is off by default. Errors use the SCIM error format (`urn:ietf:params:scim:api:messages:2.0:Error`).

### Storage

Resources are stored in process memory as received, with the `id` and `meta` assigned by the application. A restart empties the store; the next sweep fills it again. The store is not shared between processes, so the application must run as a single worker while push is used.

### In the application

The `/scim` page shows the pushed users and groups in a section **Received via push**, next to the pulled data, under the same manager restriction.
