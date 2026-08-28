# SRAM FastAPI

A FastAPI application demonstrating OIDC authentication with SURF Research Access Management (SRAM).

## Overview

This project provides a reference implementation for integrating FastAPI applications with [SRAM](https://sram.surf.nl), the identity management system used by Dutch research institutions for managing access to collaborative research services.

## What is SRAM?

SURF Research Access Management (SRAM) is a federated identity service that enables:

- **Cross-institutional collaboration**: Researchers from different institutions authenticate using their home credentials
- **Guest access**: External collaborators without institutional accounts can participate
- **Self-service group management**: Collaboration owners manage membership without IT involvement
- **Standardized claims**: Applications receive consistent user attributes regardless of home institution

## Quick Start

```bash
# Run locally
make dev

# Run tests
make test

# Deploy to production
make deploy
```

1. [Register your service with SRAM](sram-setup.md)
2. [Configure and deploy](deployment.md)
3. [Use the Python API](api-reference.md)

## Features

- Browser-based OIDC authentication with session cookies
- Token introspection for CLI/API access
- User claims extraction (email, name, entitlements, affiliations)
- Demo application with HTML templates
- Collaboration management: provision collaborations, invite users, manage admins, members and groups
- Configuration validation with helpful error messages
