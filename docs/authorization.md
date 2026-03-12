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
