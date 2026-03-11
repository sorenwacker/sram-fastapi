# API Reference

This page documents the Python modules and how to use them in your FastAPI application.

## Module Overview

| Module | Purpose |
|--------|---------|
| `sram_fastapi.auth` | Authentication and authorization |
| `sram_fastapi.config` | Configuration management |

## Authentication Module

### User

Dataclass representing an authenticated user from SRAM.

```python
from sram_fastapi.auth import User

@dataclass
class User:
    sub: str                                    # Subject identifier (unique user ID)
    email: str | None                           # User's email address
    name: str | None                            # Display name
    preferred_username: str | None              # Username
    eduperson_entitlement: list[str] | None     # SRAM group memberships
    voperson_external_affiliation: list[str] | None  # Institutional affiliations
    raw_claims: dict | None                     # All OIDC claims
```

**Create from OIDC claims:**

```python
claims = {"sub": "abc123", "email": "user@example.com", "name": "Jane Doe"}
user = User.from_claims(claims)
```

### OIDCClient

Client for SRAM OIDC authentication. Handles the OAuth2 flow.

```python
from sram_fastapi.auth import OIDCClient

client = OIDCClient(settings)

# Redirect user to SRAM login
response = await client.authorize_redirect(request, redirect_uri)

# Handle callback after authentication
token, user = await client.handle_callback(request)

# Introspect a Bearer token (for API access)
token_info = await client.introspect_token(token_string)
```

### Dependency Functions

FastAPI dependencies for protecting routes.

#### get_current_user

Requires authentication. Returns `User` or raises 401.

```python
from fastapi import Depends
from sram_fastapi.auth import User, get_current_user

@app.get("/profile")
async def profile(user: User = Depends(get_current_user)):
    return {"email": user.email}
```

#### get_optional_user

Returns `User` if authenticated, `None` otherwise.

```python
from sram_fastapi.auth import get_optional_user

@app.get("/")
async def home(user: User | None = Depends(get_optional_user)):
    if user:
        return {"message": f"Hello, {user.name}"}
    return {"message": "Hello, guest"}
```

#### get_token_user

Authenticates via Bearer token (for API/CLI access). Uses token introspection.

```python
from sram_fastapi.auth import get_token_user

@app.get("/api/data")
async def api_data(user: User = Depends(get_token_user)):
    return {"user": user.sub}
```

Client usage:

```bash
curl -H "Authorization: Bearer <access_token>" https://your-app/api/data
```

#### get_oidc_client

Returns the `OIDCClient` instance. Use in auth routes.

```python
from fastapi import Request
from sram_fastapi.auth import OIDCClient, get_oidc_client
from sram_fastapi.config import get_settings

@app.get("/auth/login")
async def login(
    request: Request,
    oidc_client: OIDCClient = Depends(get_oidc_client),
):
    settings = get_settings()
    redirect_uri = f"{settings.base_url}/auth/callback"
    return await oidc_client.authorize_redirect(request, redirect_uri)
```

## Authorization Module

### AuthorizationError

Exception raised when a user lacks required permissions.

```python
from sram_fastapi.auth import AuthorizationError

class AuthorizationError(Exception):
    required: list[str]      # What was required
    actual: list[str]        # What the user has
    check_type: str          # "entitlement" or "affiliation"
    require_all: bool        # Whether all values were required
```

Handle with a custom exception handler:

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(AuthorizationError)
async def auth_error_handler(request: Request, exc: AuthorizationError):
    return JSONResponse(
        status_code=403,
        content={
            "detail": "Access denied",
            "required": exc.required,
            "check_type": exc.check_type,
        },
    )
```

### require_entitlement

Factory that creates a dependency requiring specific SRAM group memberships.

```python
from sram_fastapi.auth import require_entitlement

# Require membership in a specific group
@app.get("/admin")
async def admin_page(
    user: User = Depends(require_entitlement(
        "urn:mace:surf.nl:sram:group:myorg:myco:admins"
    ))
):
    return {"message": "Welcome, admin"}

# Require ANY of multiple entitlements (OR logic)
@app.get("/staff")
async def staff_page(
    user: User = Depends(require_entitlement(
        "urn:mace:surf.nl:sram:group:myorg:myco:admins",
        "urn:mace:surf.nl:sram:group:myorg:myco:staff",
    ))
):
    return {"message": "Welcome, staff member"}

# Require ALL entitlements (AND logic)
@app.get("/superadmin")
async def superadmin_page(
    user: User = Depends(require_entitlement(
        "urn:mace:surf.nl:sram:group:myorg:myco:admins",
        "urn:mace:surf.nl:sram:group:myorg:myco:security",
        require_all=True,
    ))
):
    return {"message": "Welcome, superadmin"}
```

### require_affiliation

Factory that creates a dependency requiring specific institutional affiliations.

```python
from sram_fastapi.auth import require_affiliation

# Require exact affiliation
@app.get("/tudelft-staff")
async def tudelft_staff(
    user: User = Depends(require_affiliation("staff@tudelft.nl"))
):
    return {"message": "Welcome, TU Delft staff"}

# Wildcard: any organization with "staff" role
@app.get("/any-staff")
async def any_staff(
    user: User = Depends(require_affiliation("staff@"))
):
    return {"message": "Welcome, staff member"}

# Wildcard: any role at specific organization
@app.get("/tudelft-member")
async def tudelft_member(
    user: User = Depends(require_affiliation("@tudelft.nl"))
):
    return {"message": "Welcome, TU Delft member"}
```

**Affiliation pattern matching:**

| Pattern | Matches |
|---------|---------|
| `staff@tudelft.nl` | Exact match only |
| `staff@` | Any org with staff role (e.g., `staff@tudelft.nl`, `staff@uu.nl`) |
| `@tudelft.nl` | Any role at TU Delft (e.g., `staff@tudelft.nl`, `student@tudelft.nl`) |

## Configuration Module

### Settings

Pydantic settings class for configuration. Loads from environment variables or `.env` file.

```python
from sram_fastapi.config import Settings

class Settings(BaseSettings):
    # Application
    app_name: str = "SRAM FastAPI"
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # SRAM OIDC (required)
    sram_oidc_client_id: str
    sram_oidc_client_secret: str
    sram_oidc_discovery_url: str = "https://proxy.sram.surf.nl/.well-known/openid-configuration"

    # Session
    session_cookie_name: str = "session"
    session_max_age: int = 3600  # seconds
    session_https_only: bool = True

    # Server
    base_url: str = "http://localhost:8124"
    allowed_redirect_urls: list[str] = ["http://localhost:8124"]
```

### get_settings

Cached function returning the settings instance.

```python
from sram_fastapi.config import get_settings

settings = get_settings()
print(settings.sram_oidc_client_id)
```

As a FastAPI dependency:

```python
from fastapi import Depends
from sram_fastapi.config import Settings, get_settings

@app.get("/info")
async def info(settings: Settings = Depends(get_settings)):
    return {"app": settings.app_name}
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SRAM_OIDC_CLIENT_ID` | Yes | - | OIDC client ID from SRAM |
| `SRAM_OIDC_CLIENT_SECRET` | Yes | - | OIDC client secret |
| `SRAM_OIDC_DISCOVERY_URL` | No | `https://proxy.sram.surf.nl/.well-known/openid-configuration` | OIDC discovery endpoint |
| `SECRET_KEY` | Yes | - | Secret for session encryption |
| `BASE_URL` | No | `http://localhost:8124` | Public URL of your app |
| `DEBUG` | No | `false` | Enable debug mode |
| `SESSION_MAX_AGE` | No | `3600` | Session duration in seconds |
| `SESSION_HTTPS_ONLY` | No | `true` | Require HTTPS for cookies |

## Complete Example

Minimal FastAPI app with SRAM authentication:

```python
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from sram_fastapi.auth import (
    OIDCClient,
    User,
    get_current_user,
    get_oidc_client,
    get_optional_user,
)
from sram_fastapi.config import Settings, get_settings

app = FastAPI()
settings = get_settings()

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.session_https_only,
)

@app.get("/")
async def home(user: User | None = Depends(get_optional_user)):
    if user:
        return {"message": f"Hello, {user.name}"}
    return {"message": "Please log in", "login_url": "/auth/login"}

@app.get("/auth/login")
async def login(
    request: Request,
    oidc_client: OIDCClient = Depends(get_oidc_client),
):
    redirect_uri = f"{settings.base_url}/auth/callback"
    return await oidc_client.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def callback(
    request: Request,
    oidc_client: OIDCClient = Depends(get_oidc_client),
):
    token, user = await oidc_client.handle_callback(request)
    request.session["user"] = user.raw_claims
    return RedirectResponse(url="/")

@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@app.get("/profile")
async def profile(user: User = Depends(get_current_user)):
    return {
        "sub": user.sub,
        "email": user.email,
        "name": user.name,
        "entitlements": user.eduperson_entitlement,
    }
```
