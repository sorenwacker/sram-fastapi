# Authorization

This guide covers implementing authorization in your SRAM-authenticated FastAPI application.

## Authentication vs Authorization

**Authentication** answers "Who are you?" - it verifies user identity through SRAM's OIDC flow.

**Authorization** answers "What can you do?" - it determines what resources an authenticated user can access based on their attributes.

## SRAM User Attributes

SRAM provides two primary attributes for authorization decisions:

### eduperson_entitlement

A list of URIs representing specific permissions or capabilities granted to the user. Common patterns:

```
urn:mace:surf.nl:sram:group:my-collaboration
urn:example:admin
urn:example:researcher
```

Use entitlements for fine-grained access control to specific features or resources.

### voperson_external_affiliation

A list of affiliation strings in the format `role@organization`. Examples:

```
staff@tudelft.nl
employee@example.org
student@university.edu
```

Use affiliations for role-based access control at the organizational level.

## Authorization Dependencies

The `sram_fastapi.auth` module provides two dependency factories for route-level authorization.

### require_entitlement

Restricts access to users with specific entitlements.

```python
from fastapi import Depends
from sram_fastapi.auth import require_entitlement

# Require any one of the specified entitlements (OR logic)
@app.get("/admin")
async def admin_page(
    user = Depends(require_entitlement("urn:example:admin", "urn:example:superuser"))
):
    return {"message": "Welcome, admin!"}

# Require all specified entitlements (AND logic)
@app.get("/super-admin")
async def super_admin_page(
    user = Depends(require_entitlement(
        "urn:example:admin",
        "urn:example:billing",
        require_all=True
    ))
):
    return {"message": "Welcome, super admin!"}
```

### require_affiliation

Restricts access based on user affiliations. Supports wildcard matching.

```python
from fastapi import Depends
from sram_fastapi.auth import require_affiliation

# Require staff at any organization
@app.get("/staff-only")
async def staff_page(
    user = Depends(require_affiliation("staff@"))
):
    return {"message": "Welcome, staff member!"}

# Require specific organization affiliation
@app.get("/tudelft-only")
async def tudelft_page(
    user = Depends(require_affiliation("staff@tudelft.nl", "employee@tudelft.nl"))
):
    return {"message": "Welcome, TU Delft affiliate!"}

# Require multiple affiliations (AND logic)
@app.get("/staff-and-researcher")
async def staff_researcher_page(
    user = Depends(require_affiliation(
        "staff@",
        "researcher@",
        require_all=True
    ))
):
    return {"message": "Welcome, staff researcher!"}
```

### Wildcard Matching

The `require_affiliation` dependency supports two wildcard patterns:

- `role@` - Matches any organization with the specified role (e.g., `staff@` matches `staff@tudelft.nl`, `staff@example.org`)
- `@organization` - Matches any role at the specified organization (e.g., `@tudelft.nl` matches `staff@tudelft.nl`, `student@tudelft.nl`)

### require_group

Restricts access to members of a named group inside a collaboration, addressed by the group's short name rather than by a full entitlement URI.

An entitlement names one specific collaboration: `urn:mace:surf.nl:sram:group:tudelft:sramdemo:editors` grants nothing to a member of another collaboration, even one holding the equivalent group there. `require_entitlement` therefore only works for an application serving a single, known collaboration. `require_group` matches on the last segment, so the same rule holds for every collaboration the application serves.

```python
from fastapi import Depends
from sram_fastapi.auth import User, require_group


@app.get("/reports")
async def reports(user: User = Depends(require_group("editor"))):
    return {"message": "Members of the editor group can see this"}
```

Features are mapped to group short names in configuration, so a deployment can point a feature at whatever its groups are called:

```bash
# .env
SRAM_FEATURE_GROUPS=editor=tudelft:sramdemo/sramdemo-editors,reviewer=tudelft:sramdemo/sramdemo-reviewers
```

Each entry names the collaboration the group belongs to and the group's short name as it appears in the entitlement. The feature is granted to members of that group in that collaboration, and the same short name in any other collaboration grants nothing.

An entry that names only a short name parses but grants nothing, and says so in the log. A short name is chosen by whoever creates the group, so on its own it is not a capability: an admin of any collaboration connected to this service could create a group under that name and hand the feature to their members. See the next section for what would make a name trustworthy on its own, and why this application does not yet rely on it.

The check fails closed. Requiring a feature that configuration does not define denies every user and logs the missing mapping, rather than falling back to the feature name as a group. A typo therefore locks people out, which is visible, instead of granting access through a group nobody meant to name.

Like the other dependencies, `require_group` takes several names and a `require_all` flag:

```python
Depends(require_group("editor", "curator"))                    # either group
Depends(require_group("editor", "curator", require_all=True))  # both groups
```

To find which collaborations granted a feature, rather than only whether one did, use `groups_of(user)`, which returns the collaboration URN and short name of every group the user belongs to.

## Where the groups come from

The short names have to exist in each collaboration for any of this to work. SRAM provides that through **service groups**: groups defined once on the service, which SRAM provisions into every collaboration that connects the service.

This division of labour is what makes an application able to depend on a short name:

| Action | Service admin | Collaboration admin |
|--------|---------------|---------------------|
| Define the group and its short name | Yes, on the service in SRAM | No |
| Have it appear in a collaboration | Automatic when the service is connected | No |
| Add and remove members | No | Yes |
| Turn on auto provisioning, so every member joins | No | Yes |
| Rename or delete it | Yes, on the service | No |

A collaboration admin cannot remove a group that a connected service provisioned, so the application's authorization cannot be broken from inside a collaboration. What each collaboration decides for itself is who is in the group.

A collaboration admin can also create ordinary groups. Those work identically for authorization, but nothing guarantees the short name, so an application should rely on service groups for its own features.

### Why two applications can both define a group called "editors"

An organisation may connect several applications to the same collaboration, and nothing stops two of them from naming a service group `editors`. Matching on a bare short name would then let a member of one application's editors group pass the other application's check.

SRAM prevents this when it provisions the group. The group it creates in the collaboration is named after both the service and the service group:

```
short_name  = <service abbreviation>-<service group short name>
platform id = <organisation>:<collaboration>:<short_name>
```

Two applications defining `editors` therefore produce `appa-editors` and `appb-editors` as separate groups in the same collaboration, with separate memberships and separate entitlements. The prefix is applied by SRAM, not by the application, and a service group's short name cannot be changed from inside a collaboration, so the namespace holds.

This is why a feature maps to the prefixed name. Configuring `editor=editors` would match nothing, because no entitlement ends in `:editors` once SRAM has prefixed it; and stripping prefixes before matching would reintroduce exactly the collision the prefix exists to prevent.

A second boundary limits the damage of any name collision that does occur. SRAM releases only the memberships that concern the application being logged into: "Applications only receive attributes which concern the collaboration(s) to which the application is connected." A group named after this application in a collaboration it is not connected to never appears in a claim it receives.

This leaves one case the application has to handle itself, and it is the reason every feature has to name its collaboration.

The prefix looks like it should be enough. A name of the form `<abbreviation>-<group>` is created by SRAM, and in a collaboration where the corresponding service group exists, the name is occupied and a collaboration admin cannot take it. But that holds only for names that correspond to a service group that really exists. If a deployment configures `sramdemo-editors` while the service group is called `editor`, or before it is created at all, the name is unclaimed everywhere, and an admin of any collaboration connected to this service can create an ordinary group under it. The prefix is a naming convention, not proof of origin, and comparing strings cannot tell the two apart.

Establishing origin needs data the application does not have at login: the group's `service_group_id`, which the organisation API returns and which marks the groups SRAM provisioned for a service. Until the application resolves configured features against that, a name alone is not trusted, and each feature is bound to the collaboration whose group grants it. The cost is a configuration entry per collaboration; the alternative is trusting a string that anyone connected to the service can create.

## Handling Authorization Errors

When authorization fails, an `AuthorizationError` exception is raised. This exception contains context about what was required vs what the user has.

### In API Routes

For JSON APIs, the default behavior returns a 403 Forbidden response:

```json
{
    "detail": "Access denied: missing required entitlements"
}
```

### In Web Applications

For HTML applications, register an exception handler to render a user-friendly error page:

```python
from fastapi import Request
from sram_fastapi.auth import AuthorizationError

@app.exception_handler(AuthorizationError)
async def authorization_error_handler(request: Request, exc: AuthorizationError):
    return templates.TemplateResponse(
        request=request,
        name="forbidden.html",
        status_code=403,
        context={
            "required": exc.required,
            "actual": exc.actual,
            "check_type": exc.check_type,
        },
    )
```

### AuthorizationError Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `required` | `list[str]` | The entitlements or affiliations that were required |
| `actual` | `list[str]` | The entitlements or affiliations the user actually has |
| `check_type` | `str` | Either `"entitlement"` or `"affiliation"` |
| `require_all` | `bool` | Whether all requirements needed to match (`True`) or just one (`False`) |

## Complete Example

```python
from typing import Annotated
from fastapi import Depends, FastAPI, Request
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import (
    AuthorizationError,
    User,
    get_current_user,
    require_affiliation,
    require_entitlement,
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Exception handler for authorization errors
@app.exception_handler(AuthorizationError)
async def authz_error_handler(request: Request, exc: AuthorizationError):
    return templates.TemplateResponse(
        request=request,
        name="forbidden.html",
        status_code=403,
        context={
            "required": exc.required,
            "actual": exc.actual,
            "check_type": exc.check_type,
        },
    )

# Authenticated route (no additional authorization)
@app.get("/")
async def home(user: Annotated[User, Depends(get_current_user)]):
    return {"user": user.name}

# Staff-only route
@app.get("/staff")
async def staff_area(
    user: Annotated[User, Depends(require_affiliation("staff@"))]
):
    return {"message": f"Welcome staff member {user.name}"}

# Admin route requiring specific entitlement
@app.get("/admin")
async def admin_area(
    user: Annotated[User, Depends(require_entitlement("urn:example:admin"))]
):
    return {"message": f"Welcome admin {user.name}"}
```

## Best Practices

1. **Use entitlements for feature access** - Grant specific capabilities through SRAM collaboration entitlements
2. **Use affiliations for organizational access** - Restrict access based on institutional roles
3. **Prefer specific requirements** - Use exact entitlement URNs rather than patterns where possible
4. **Handle errors gracefully** - Provide clear feedback about what access is required
5. **Log authorization decisions** - Track access attempts for security auditing
6. **Test authorization logic** - Write unit tests for your authorization requirements
