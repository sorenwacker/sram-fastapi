# SRAM FastAPI

A FastAPI application demonstrating OIDC authentication with SURF Research Access Management (SRAM).

## Overview

This project provides a reference implementation for integrating FastAPI applications with [SRAM](https://sram.surf.nl), the identity management system used by Dutch research institutions for managing access to collaborative research services.

### What is SRAM?

SURF Research Access Management (SRAM) is a federated identity service that enables:

- **Cross-institutional collaboration**: Researchers from different institutions authenticate using their home credentials
- **Guest access**: External collaborators without institutional accounts can participate
- **Self-service group management**: Collaboration owners manage membership without IT involvement
- **Standardized claims**: Applications receive consistent user attributes regardless of home institution

### Why this project?

Integrating OIDC authentication requires handling authorization flows, token management, and session handling. This project provides:

- A working implementation of SRAM OIDC authentication in FastAPI
- Both browser-based (session) and API (token introspection) authentication patterns
- A demo application for testing the authentication flow
- Reusable components for building SRAM-authenticated services

## Documentation

Full documentation is published on GitHub Pages at [sorenwacker.github.io/sram-fastapi](https://sorenwacker.github.io/sram-fastapi/):

- [API Reference](https://sorenwacker.github.io/sram-fastapi/api-reference/): Python modules and how to use them in your FastAPI application
- [Authorization](https://sorenwacker.github.io/sram-fastapi/authorization/): implementing authorization in a SRAM-authenticated application
- [Collaboration Management](https://sorenwacker.github.io/sram-fastapi/collaboration-management/): provisioning collaborations and managing members through the SRAM organisation API
- [SRAM Setup](https://sorenwacker.github.io/sram-fastapi/sram-setup/): registering the application as a service in SRAM and connecting it to collaborations
- [Deployment](https://sorenwacker.github.io/sram-fastapi/deployment/): deploying the application to production

The documentation sources live in `docs/` and are built with mkdocs (`uv run mkdocs serve` for a local preview).

## Features

- Browser-based OIDC authentication with session cookies
- Token introspection for CLI/API access
- User claims extraction (email, name, entitlements, affiliations)
- Demo application with HTML templates
- Collaboration management: provision collaborations, invite users, manage admins, members and groups
- Configuration validation with helpful error messages

## Project Structure

```
src/sram_fastapi/
    __init__.py
    config.py          # Settings management with validation
    auth.py            # OIDC client and authentication dependencies
    main.py            # API-only FastAPI application
    collaborations.py  # SRAM organisation API client
    demo/
        app.py         # Demo application with HTML templates
        routers/       # Page, auth, authorization and collaboration routes
        templates/     # Jinja2 templates for demo UI
```

## Requirements

- Python 3.11+
- A SRAM collaboration with an OIDC application configured

## Installation

```bash
uv sync
```

For development:

```bash
uv sync --extra dev
```

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Configure the required variables:

| Variable | Description |
|----------|-------------|
| `SRAM_OIDC_CLIENT_ID` | OIDC client ID from SRAM application settings |
| `SRAM_OIDC_CLIENT_SECRET` | OIDC client secret from SRAM application settings |
| `SECRET_KEY` | Random string for session encryption (use `openssl rand -hex 32`) |
| `BASE_URL` | Public URL where the application is deployed |

Optional variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SRAM_OIDC_DISCOVERY_URL` | SRAM proxy URL | OIDC discovery endpoint |
| `SESSION_MAX_AGE` | 3600 | Session duration in seconds |
| `DEBUG` | false | Enable debug mode |

## Usage

### Demo Application

The demo application provides a web interface to test the authentication flow:

```bash
uv run uvicorn sram_fastapi.demo.app:get_demo_app --factory --reload --port 8124
```

Open http://localhost:8124 to access the demo.

Demo endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /` | Home page with login status |
| `GET /profile` | User profile with claims (requires login) |
| `GET /privacy` | Privacy policy |
| `GET /aup` | Acceptable use policy |
| `GET /auth/login` | Initiate SRAM login |
| `GET /auth/logout` | Clear session |

### API Server

For API-only deployments without the demo UI:

```bash
uv run uvicorn sram_fastapi.main:get_app --factory --reload --port 8124
```

API endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /` | Status endpoint |
| `GET /auth/me` | Current user info (requires session) |
| `GET /api/protected` | Protected endpoint (requires Bearer token) |
| `GET /health` | Health check |

### API Authentication

For programmatic access, use Bearer token authentication:

```bash
curl -H "Authorization: Bearer YOUR_SRAM_TOKEN" https://your-app.example.com/api/protected
```

Tokens can be generated in SRAM for applications that support token-based access.

## SRAM Application Setup

1. Log in to [SRAM](https://sram.surf.nl)
2. Navigate to your collaboration
3. Go to Applications and create a new application
4. Configure OIDC settings:
   - **Redirect URL**: `{BASE_URL}/auth/callback`
   - **Privacy Policy URL**: `{BASE_URL}/privacy`
   - **Acceptable Use Policy URL**: `{BASE_URL}/aup`
5. Copy the client ID and secret to your `.env` file

See the [SRAM documentation](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226073/SURF+Research+Access+Management) for detailed instructions.

## Development

### Running Tests

```bash
uv run pytest
```

With coverage:

```bash
uv run pytest --cov=sram_fastapi
```

### Code Quality

Pre-commit hooks run ruff for linting and formatting:

```bash
uv run pre-commit run --all-files
```

## Deployment

For production deployment:

1. Set `DEBUG=false`
2. Generate a secure `SECRET_KEY`
3. Configure `BASE_URL` to your production domain
4. Use HTTPS (required for secure cookies)
5. Consider running behind a reverse proxy (nginx, traefik)

Example with gunicorn:

```bash
uv run gunicorn sram_fastapi.demo.app:get_demo_app --factory -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8124
```

## License

MIT
