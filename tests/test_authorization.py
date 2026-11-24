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
                "employee@example.org",
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
            "employee@",
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


class TestDemoDeniedResponseFormat:
    """The demo's test buttons call JSON endpoints with fetch and parse the answer."""

    def client(self):
        """A demo client whose user lacks the demo entitlement."""
        from fastapi.testclient import TestClient

        from sram_fastapi.auth import get_current_user
        from sram_fastapi.config import Settings
        from sram_fastapi.demo.app import create_demo_app

        app = create_demo_app(
            Settings(
                _env_file=None,
                secret_key="test-secret-key-at-least-32-characters",
                sram_oidc_client_id="a",
                sram_oidc_client_secret="b",
            )
        )
        app.dependency_overrides[get_current_user] = lambda: User.from_claims(
            {"sub": "s", "eduperson_entitlement": ["urn:example:other"]}
        )
        return TestClient(app)

    def test_fetch_gets_json(self):
        """A request that does not ask for HTML gets a JSON 403 with a detail."""
        response = self.client().get("/demo/entitlement-protected", headers={"Accept": "*/*"})
        assert response.status_code == 403
        assert "Access denied" in response.json()["detail"]

    def test_browser_navigation_gets_page(self):
        """A browser navigating to the URL still sees the access denied page."""
        response = self.client().get("/demo/entitlement-protected", headers={"Accept": "text/html"})
        assert response.status_code == 403
        assert "text/html" in response.headers["content-type"]


class TestGroupTestButtons:
    """Per-group access checks on the home page and the SCIM page."""

    GROUP = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:group1"
    COLLABORATION = "urn:mace:surf.nl:sram:group:tudelft:sramdemo"

    def app(self, entitlements):
        """A demo app with a session user holding the given entitlements."""
        from sram_fastapi.auth import get_current_user, get_optional_user
        from sram_fastapi.config import Settings
        from sram_fastapi.demo.app import create_demo_app

        app = create_demo_app(
            Settings(
                _env_file=None,
                secret_key="test-secret-key-at-least-32-characters",
                sram_oidc_client_id="a",
                sram_oidc_client_secret="b",
            )
        )
        user = User.from_claims({"sub": "s", "eduperson_entitlement": entitlements})
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_user] = lambda: user
        return app

    def test_member_passes(self):
        """A member of the group gets 200."""
        from fastapi.testclient import TestClient

        client = TestClient(self.app([self.GROUP]))
        assert client.get("/demo/groups/tudelft:sramdemo/group1").status_code == 200

    def test_non_member_is_refused(self):
        """Any other group answers 403 as JSON."""
        from fastapi.testclient import TestClient

        client = TestClient(self.app([self.GROUP]))
        response = client.get("/demo/groups/tudelft:sramdemo/other", headers={"Accept": "*/*"})
        assert response.status_code == 403
        assert "detail" in response.json()

    def test_home_lists_own_groups_only(self):
        """Groups from the claims get a button; the plain collaboration does not."""
        from fastapi.testclient import TestClient

        html = TestClient(self.app([self.COLLABORATION, self.GROUP])).get("/").text
        assert 'data-test-url="/demo/groups/tudelft:sramdemo/group1"' in html
        assert 'data-test-url="/demo/groups/tudelft:sramdemo/"' not in html
        assert html.count('data-test-url="/demo/groups/') == 1


def test_test_buttons_never_put_server_text_into_html():
    """Server messages echo group names, so the button script must insert them as text."""
    import re
    from pathlib import Path

    import sram_fastapi

    base = Path(sram_fastapi.__file__).parent / "demo" / "templates" / "base.html"
    script = base.read_text().split("<script>", 1)[1]
    assignments = re.findall(r"innerHTML\s*=([^;]*);", script)
    assert [a for a in assignments if "data." in a or "e.message" in a] == []


class TestForbiddenPage:
    """The access denied page gives guidance that matches the failed check."""

    def render(self, check_type: str) -> str:
        """Render forbidden.html for a denial of the given type."""
        from sram_fastapi.demo.templating import create_templates

        template = create_templates().env.get_template("forbidden.html")
        return template.render(
            request=None,
            user=None,
            required=["x"],
            actual=[],
            check_type=check_type,
            require_all=False,
            nav_is_manager=lambda request, user: False,
        )

    def test_group_denial_explains_group_membership(self):
        """A failed group check talks about groups, not institutional affiliation."""
        html = self.render("group")
        assert "group" in html.split("How to Get Access", 1)[1].lower()
        assert "identity management team" not in html

    def test_no_link_to_missing_route(self):
        """The page links only to routes that exist."""
        assert "/demo/authorization" not in self.render("entitlement")


def test_unused_redirect_setting_is_gone():
    """allowed_redirect_urls was never enforced, so it must not suggest protection."""
    from sram_fastapi.config import Settings

    assert "allowed_redirect_urls" not in Settings.model_fields
