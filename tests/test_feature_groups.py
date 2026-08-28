"""Tests for feature access granted through SRAM groups."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from sram_fastapi.auth import (
    AuthorizationError,
    User,
    get_current_user,
    require_group,
)
from sram_fastapi.collaborations import groups_of
from sram_fastapi.config import FeatureGroup, Settings, get_settings

EDITORS = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemo-editors"
REVIEWERS = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemo-reviewers"
OTHER_APP_EDITORS = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:otherapp-editors"
COLLABORATION = "urn:mace:surf.nl:sram:group:tudelft:sramdemo"


def make_settings(feature_groups: str, abbreviation: str = "sramdemo") -> Settings:
    """Build settings with a feature group mapping."""
    return Settings(
        secret_key="test-secret-key",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_feature_groups=feature_groups,
        sram_service_abbreviation=abbreviation,
    )


def user_with(*entitlements: str) -> User:
    """Build a user holding the given entitlements."""
    return User.from_claims({"sub": "user-1", "eduperson_entitlement": list(entitlements)})


class TestFeatureGroupSettings:
    """Tests for parsing the feature group mapping."""

    def test_pairs(self):
        """Each pair maps a feature name to a group short name."""
        settings = make_settings("editor=sramdemo-editors, reviewer=sramdemo-reviewers")
        assert settings.feature_groups == {
            "editor": FeatureGroup(short_name="sramdemo-editors"),
            "reviewer": FeatureGroup(short_name="sramdemo-reviewers"),
        }

    def test_bare_name_maps_to_itself(self):
        """A bare name is both the feature and the group short name."""
        assert make_settings("sramdemo-editors").feature_groups == {
            "sramdemo-editors": FeatureGroup(short_name="sramdemo-editors")
        }

    def test_collaboration_scoped_entry(self):
        """A value carrying a collaboration binds the group to that collaboration."""
        assert make_settings("demo=tudelft:sramdemo/group1").feature_groups == {
            "demo": FeatureGroup(short_name="group1", collaboration="tudelft:sramdemo")
        }

    def test_empty_setting(self):
        """No mapping means no features are defined."""
        assert make_settings("").feature_groups == {}


class TestGroupsOf:
    """Tests for reading group memberships out of the entitlement claim."""

    def test_returns_collaboration_and_short_name(self):
        """Each group entitlement yields its collaboration and its short name."""
        user = user_with(EDITORS, COLLABORATION, "urn:something:else")
        assert groups_of(user.eduperson_entitlement) == {("tudelft:sramdemo", "sramdemo-editors")}

    def test_ignores_collaboration_level_entitlements(self):
        """A collaboration membership is not a group membership."""
        assert groups_of([COLLABORATION]) == set()


class TestRequireGroup:
    """Tests for the require_group dependency."""

    def build(self, feature_groups: str, user: User, abbreviation: str = "sramdemo") -> TestClient:
        """Build an app whose route requires the editor feature."""
        app = FastAPI()
        settings = make_settings(feature_groups, abbreviation=abbreviation)

        @app.get("/reports")
        async def reports(caller: User = Depends(require_group("editor"))) -> dict:
            return {"user": caller.sub}

        @app.exception_handler(AuthorizationError)
        async def handler(request, exc: AuthorizationError):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": str(exc)})

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    def test_member_of_the_group_is_allowed(self):
        """A member of the mapped group reaches the route."""
        http = self.build("editor=sramdemo-editors", user_with(EDITORS))
        assert http.get("/reports").status_code == 200

    def test_non_member_is_refused(self):
        """A user without the group is refused."""
        http = self.build("editor=sramdemo-editors", user_with(COLLABORATION))
        assert http.get("/reports").status_code == 403

    def test_another_application_group_does_not_grant_the_feature(self):
        """A same-named group belonging to another service grants nothing."""
        http = self.build("editor=sramdemo-editors", user_with(OTHER_APP_EDITORS))
        assert http.get("/reports").status_code == 403

    def test_unconfigured_feature_denies_everyone(self):
        """A feature the deployment does not define fails closed."""
        http = self.build("reviewer=sramdemo-reviewers", user_with(EDITORS))
        assert http.get("/reports").status_code == 403

    def test_group_in_any_collaboration_grants_the_feature(self):
        """The same group in another collaboration grants the feature there too."""
        other = "urn:mace:surf.nl:sram:group:uva:climate:sramdemo-editors"
        http = self.build("editor=sramdemo-editors", user_with(other))
        assert http.get("/reports").status_code == 200


class TestRequireGroupCombinations:
    """Tests for requiring several features at once."""

    def check(self, dependency, user: User) -> int:
        """Return the status code of a route guarded by the dependency."""
        app = FastAPI()
        settings = make_settings("editor=sramdemo-editors, reviewer=sramdemo-reviewers")

        @app.get("/x")
        async def route(caller: User = Depends(dependency)) -> dict:
            return {"ok": True}

        @app.exception_handler(AuthorizationError)
        async def handler(request, exc: AuthorizationError):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": str(exc)})

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app).get("/x").status_code

    def test_any_of_the_features(self):
        """One of the named features suffices by default."""
        assert self.check(require_group("editor", "reviewer"), user_with(EDITORS)) == 200

    def test_all_of_the_features(self):
        """With require_all, every feature is needed."""
        assert (
            self.check(require_group("editor", "reviewer", require_all=True), user_with(EDITORS))
            == 403
        )
        assert (
            self.check(
                require_group("editor", "reviewer", require_all=True), user_with(EDITORS, REVIEWERS)
            )
            == 200
        )


@pytest.mark.parametrize("feature", ["editor", "reviewer"])
def test_demo_exposes_a_route_per_configured_feature(feature: str):
    """The demo application serves one protected route for each configured feature."""
    from sram_fastapi.demo.app import create_demo_app

    settings = Settings(
        secret_key="test-secret-key",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_feature_groups="editor=sramdemo-editors, reviewer=sramdemo-reviewers",
    )
    routes = {route.path for route in create_demo_app(settings).routes}
    assert f"/demo/features/{feature}" in routes


def test_home_page_lists_configured_features():
    """The home page shows each feature, its group and whether the user is a member."""
    from sram_fastapi.auth import get_optional_user
    from sram_fastapi.demo.app import create_demo_app

    settings = Settings(
        secret_key="test-secret-key",
        sram_oidc_client_id="test-client-id",
        sram_oidc_client_secret="test-client-secret",
        sram_feature_groups="editor=sramdemo-editors, reviewer=sramdemo-reviewers",
    )
    app = create_demo_app(settings)
    app.dependency_overrides[get_optional_user] = lambda: user_with(EDITORS)

    page = TestClient(app).get("/").text

    assert "sramdemo-editors" in page
    assert "sramdemo-reviewers" in page
    assert 'data-test-url="/demo/features/editor"' in page
    assert "member" in page


class TestUnprefixedGroupsAreNotTrusted:
    """Tests that a short name anyone could choose cannot grant a feature."""

    def build(self, feature_groups: str, user: User, abbreviation: str = "sramdemo") -> TestClient:
        """Build an app whose route requires the demo feature."""
        app = FastAPI()
        settings = make_settings(feature_groups, abbreviation=abbreviation)

        @app.get("/x")
        async def route(caller: User = Depends(require_group("demo"))) -> dict:
            return {"ok": True}

        @app.exception_handler(AuthorizationError)
        async def handler(request, exc: AuthorizationError):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": str(exc)})

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    def test_group_without_the_service_prefix_grants_nothing(self):
        """An ordinary group name is not trusted across collaborations."""
        held = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:group1"
        assert self.build("demo=group1", user_with(held)).get("/x").status_code == 403

    def test_prefixed_group_is_trusted_anywhere(self):
        """A service group carries this service's abbreviation and is trusted."""
        held = "urn:mace:surf.nl:sram:group:uva:climate:sramdemo-editors"
        assert self.build("demo=sramdemo-editors", user_with(held)).get("/x").status_code == 200

    def test_prefix_of_another_service_grants_nothing(self):
        """Another service's group is not this service's group."""
        held = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:otherapp-editors"
        assert self.build("demo=otherapp-editors", user_with(held)).get("/x").status_code == 403

    def test_unset_abbreviation_denies_unscoped_names(self):
        """Without a configured abbreviation no unscoped name can be trusted."""
        held = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemo-editors"
        http = self.build("demo=sramdemo-editors", user_with(held), abbreviation="")
        assert http.get("/x").status_code == 403


class TestCollaborationScopedFeatures:
    """Tests for features bound to one named collaboration."""

    def build(self, feature_groups: str, user: User) -> TestClient:
        """Build an app whose route requires the demo feature."""
        app = FastAPI()
        settings = make_settings(feature_groups)

        @app.get("/x")
        async def route(caller: User = Depends(require_group("demo"))) -> dict:
            return {"ok": True}

        @app.exception_handler(AuthorizationError)
        async def handler(request, exc: AuthorizationError):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": str(exc)})

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    def test_group_in_the_named_collaboration_grants_the_feature(self):
        """An ordinary group grants the feature inside the collaboration it was bound to."""
        held = "urn:mace:surf.nl:sram:group:tudelft:sramdemo:group1"
        http = self.build("demo=tudelft:sramdemo/group1", user_with(held))
        assert http.get("/x").status_code == 200

    def test_same_name_elsewhere_grants_nothing(self):
        """The same short name in another collaboration does not grant the feature."""
        held = "urn:mace:surf.nl:sram:group:uva:climate:group1"
        http = self.build("demo=tudelft:sramdemo/group1", user_with(held))
        assert http.get("/x").status_code == 403
