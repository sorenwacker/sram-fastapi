"""Tests for authorization module."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from sram_fastapi.auth import (
    AuthorizationError,
    User,
    require_affiliation,
    require_entitlement,
)


@pytest.fixture
def user_with_entitlements() -> User:
    """Create a user with entitlements."""
    return User.from_claims(
        {
            "sub": "user123",
            "email": "user@example.com",
            "name": "Test User",
            "eduperson_entitlement": [
                "urn:example:admin",
                "urn:example:researcher",
            ],
            "voperson_external_affiliation": [
                "staff@tudelft.nl",
                "member@example.org",
            ],
        }
    )


@pytest.fixture
def user_without_entitlements() -> User:
    """Create a user without entitlements."""
    return User.from_claims(
        {
            "sub": "user456",
            "email": "basic@example.com",
            "name": "Basic User",
            "eduperson_entitlement": [],
            "voperson_external_affiliation": [],
        }
    )


@pytest.fixture
def user_with_none_entitlements() -> User:
    """Create a user with None entitlements (not in claims)."""
    return User.from_claims(
        {
            "sub": "user789",
            "email": "minimal@example.com",
        }
    )


class TestAuthorizationError:
    """Tests for AuthorizationError exception."""

    def test_authorization_error_attributes(self):
        """AuthorizationError stores all required context."""
        exc = AuthorizationError(
            required=["urn:example:admin"],
            actual=["urn:example:user"],
            check_type="entitlement",
            require_all=False,
        )

        assert exc.required == ["urn:example:admin"]
        assert exc.actual == ["urn:example:user"]
        assert exc.check_type == "entitlement"
        assert exc.require_all is False

    def test_authorization_error_message(self):
        """AuthorizationError has descriptive message."""
        exc = AuthorizationError(
            required=["urn:example:admin"],
            actual=["urn:example:user"],
            check_type="entitlement",
            require_all=False,
        )

        assert "entitlement" in str(exc)


class TestRequireEntitlement:
    """Tests for require_entitlement dependency factory."""

    def test_require_entitlement_passes_with_matching(self, user_with_entitlements):
        """Access granted when user has required entitlement."""
        check = require_entitlement("urn:example:admin")
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_entitlement_fails_without_matching(self, user_with_entitlements):
        """Access denied when user lacks required entitlement."""
        check = require_entitlement("urn:example:superuser")
        with pytest.raises(AuthorizationError) as exc_info:
            check(user_with_entitlements)

        assert exc_info.value.check_type == "entitlement"
        assert "urn:example:superuser" in exc_info.value.required

    def test_require_entitlement_or_logic(self, user_with_entitlements):
        """Access granted when user has any of multiple entitlements (OR)."""
        check = require_entitlement("urn:example:admin", "urn:example:superuser")
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_entitlement_and_logic_passes(self, user_with_entitlements):
        """Access granted when user has all required entitlements (AND)."""
        check = require_entitlement(
            "urn:example:admin",
            "urn:example:researcher",
            require_all=True,
        )
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_entitlement_and_logic_fails(self, user_with_entitlements):
        """Access denied when user lacks any required entitlement (AND)."""
        check = require_entitlement(
            "urn:example:admin",
            "urn:example:superuser",
            require_all=True,
        )
        with pytest.raises(AuthorizationError) as exc_info:
            check(user_with_entitlements)

        assert exc_info.value.require_all is True

    def test_require_entitlement_empty_user_entitlements(self, user_without_entitlements):
        """Access denied when user has empty entitlements list."""
        check = require_entitlement("urn:example:admin")
        with pytest.raises(AuthorizationError):
            check(user_without_entitlements)

    def test_require_entitlement_none_user_entitlements(self, user_with_none_entitlements):
        """Access denied when user has no entitlements claim."""
        check = require_entitlement("urn:example:admin")
        with pytest.raises(AuthorizationError):
            check(user_with_none_entitlements)


class TestRequireAffiliation:
    """Tests for require_affiliation dependency factory."""

    def test_require_affiliation_exact_match(self, user_with_entitlements):
        """Access granted with exact affiliation match."""
        check = require_affiliation("staff@tudelft.nl")
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_affiliation_fails_without_matching(self, user_with_entitlements):
        """Access denied when user lacks required affiliation."""
        check = require_affiliation("admin@example.org")
        with pytest.raises(AuthorizationError) as exc_info:
            check(user_with_entitlements)

        assert exc_info.value.check_type == "affiliation"

    def test_require_affiliation_wildcard_role(self, user_with_entitlements):
        """Wildcard role@ matches any organization."""
        check = require_affiliation("staff@")
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_affiliation_wildcard_role_no_match(self, user_with_entitlements):
        """Wildcard role@ fails when user lacks role."""
        check = require_affiliation("student@")
        with pytest.raises(AuthorizationError):
            check(user_with_entitlements)

    def test_require_affiliation_wildcard_org(self, user_with_entitlements):
        """Wildcard @org matches any role at organization."""
        check = require_affiliation("@tudelft.nl")
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_affiliation_wildcard_org_no_match(self, user_with_entitlements):
        """Wildcard @org fails when user not affiliated with org."""
        check = require_affiliation("@university.edu")
        with pytest.raises(AuthorizationError):
            check(user_with_entitlements)

    def test_require_affiliation_or_logic(self, user_with_entitlements):
        """Access granted when user has any of multiple affiliations (OR)."""
        check = require_affiliation("student@tudelft.nl", "staff@tudelft.nl")
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_affiliation_and_logic_passes(self, user_with_entitlements):
        """Access granted when user has all required affiliations (AND)."""
        check = require_affiliation(
            "staff@",
            "member@",
            require_all=True,
        )
        result = check(user_with_entitlements)
        assert result == user_with_entitlements

    def test_require_affiliation_and_logic_fails(self, user_with_entitlements):
        """Access denied when user lacks any required affiliation (AND)."""
        check = require_affiliation(
            "staff@",
            "student@",
            require_all=True,
        )
        with pytest.raises(AuthorizationError):
            check(user_with_entitlements)

    def test_require_affiliation_empty_user_affiliations(self, user_without_entitlements):
        """Access denied when user has empty affiliations list."""
        check = require_affiliation("staff@")
        with pytest.raises(AuthorizationError):
            check(user_without_entitlements)

    def test_require_affiliation_none_user_affiliations(self, user_with_none_entitlements):
        """Access denied when user has no affiliations claim."""
        check = require_affiliation("staff@")
        with pytest.raises(AuthorizationError):
            check(user_with_none_entitlements)


class TestAuthorizationInRoutes:
    """Integration tests for authorization in FastAPI routes."""

    def test_entitlement_route_with_valid_user(self, user_with_entitlements):
        """Route accessible with valid entitlements via dependency override."""
        from sram_fastapi.auth import get_current_user

        app = FastAPI()

        @app.get("/entitlement-protected")
        async def entitlement_route(user: User = Depends(require_entitlement("urn:example:admin"))):
            return {"user": user.sub}

        app.dependency_overrides[get_current_user] = lambda: user_with_entitlements

        with TestClient(app) as client:
            response = client.get("/entitlement-protected")
            assert response.status_code == 200
            assert response.json()["user"] == "user123"

    def test_entitlement_route_without_entitlement(self, user_without_entitlements):
        """Route returns 403 without required entitlement."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        from sram_fastapi.auth import get_current_user

        app = FastAPI()

        @app.exception_handler(AuthorizationError)
        async def authz_error_handler(request: Request, exc: AuthorizationError):
            return JSONResponse(
                status_code=403,
                content={"detail": str(exc), "check_type": exc.check_type},
            )

        @app.get("/entitlement-protected")
        async def entitlement_route(user: User = Depends(require_entitlement("urn:example:admin"))):
            return {"user": user.sub}

        app.dependency_overrides[get_current_user] = lambda: user_without_entitlements

        with TestClient(app) as client:
            response = client.get("/entitlement-protected")
            assert response.status_code == 403
            assert response.json()["check_type"] == "entitlement"

    def test_affiliation_route_with_valid_user(self, user_with_entitlements):
        """Route accessible with valid affiliations via dependency override."""
        from sram_fastapi.auth import get_current_user

        app = FastAPI()

        @app.get("/affiliation-protected")
        async def affiliation_route(user: User = Depends(require_affiliation("staff@"))):
            return {"user": user.sub}

        app.dependency_overrides[get_current_user] = lambda: user_with_entitlements

        with TestClient(app) as client:
            response = client.get("/affiliation-protected")
            assert response.status_code == 200
            assert response.json()["user"] == "user123"

    def test_affiliation_route_without_affiliation(self, user_without_entitlements):
        """Route returns 403 without required affiliation."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        from sram_fastapi.auth import get_current_user

        app = FastAPI()

        @app.exception_handler(AuthorizationError)
        async def authz_error_handler(request: Request, exc: AuthorizationError):
            return JSONResponse(
                status_code=403,
                content={"detail": str(exc), "check_type": exc.check_type},
            )

        @app.get("/affiliation-protected")
        async def affiliation_route(user: User = Depends(require_affiliation("staff@"))):
            return {"user": user.sub}

        app.dependency_overrides[get_current_user] = lambda: user_without_entitlements

        with TestClient(app) as client:
            response = client.get("/affiliation-protected")
            assert response.status_code == 403
            assert response.json()["check_type"] == "affiliation"
