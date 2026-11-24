# Code Review 261124

Review of all 25 source files and templates under `src/sram_fastapi` (3,523 lines of Python). Six reviewer agents each read one module group; every high and medium finding was then checked by a separate agent that tried to refute it. Low findings that the reviewers reported but no verifier checked are listed in the appendix.

## Baseline gates

| Gate | Result |
|------|--------|
| ruff check | pass |
| ruff format | pass |
| vulture, min confidence 80 | no findings; not configured as a pre-commit hook |
| pytest | 228 passed |
| File size, 1000 lines | pass; largest `collaborations.py`, 884 |
| Type checker | none configured |

Gaps against the project rules: vulture is not a pre-commit hook, and ruff is not a declared dev dependency, so `make lint` fails unless ruff is installed separately.

## Summary

47 findings: 15 confirmed, 1 refuted, 31 low findings not verified.

Recurring themes:

- Security hardening: a known default session key is accepted, the button script writes server text into `innerHTML`, state-changing forms and logout have no CSRF protection.
- `main.py` is a second, diverging copy of the demo app's composition and auth routes, and still identifies users by `sub`.
- Collaborators are discovered instead of injected: module-level singletons for the OIDC client, SCIM store and service group index; templating and other routers import authorization helpers from a router module.
- Incomplete handling of external input: SCIM pull ignores pagination, SCIM push answers 500 on malformed bodies.
- Dead or unconnected code: `allowed_redirect_urls`, `get_app`, `list_members`, a link to a non-existent route.

Production note for the high finding: the deployment sets `SECRET_KEY` from the vault (`vault_secret_key`), so the live site does not use the default. The defect is that the application starts without it.

## Confirmed findings

### High

#### Insecure default session secret_key

`src/sram_fastapi/config.py:38`, correctness

`secret_key: str = "change-me-in-production"` signs the Starlette session cookie. If SECRET_KEY is not set, the app starts anyway with a publicly known key, so anyone can forge a session holding any user and entitlements. Nothing rejects the default at startup.

Fix: Make secret_key required, or add a validator that rejects the placeholder (and short keys) when debug is False. Add a test for it.

Verification: The finding is real. In `/Users/sdrwacker/workspace/sram-demo/sram-fastapi/src/sram_fastapi/config.py`, line 38 declares `secret_key: str = "change-me-in-production"`. The `Settings` class has no validator for this field, and `get_settings()` only reports missing required fields. `secret_key` has a default, so it is never reported as missing. `main.py` line 34 and `demo/app.py` line 87 pass `settings.secret_key` into the session middleware. Starlette's `SessionMiddleware` signs the cookie with itsdangerous using that key. If `SECRET_KEY` is not set, the app starts and signs sessions with this public key, so anyone can forge a session. There is also no test that rejects the placeholder value. Severity stays high because the problem breaks authentication and can be reached through a single missing environment variable.

### Medium

#### SCIM pull ignores pagination

`src/sram_fastapi/scim.py:159`, correctness

_list issues a single GET and returns `response.json().get("Resources")`. SCIM ListResponses may be paged (totalResults > itemsPerPage); later pages are silently dropped, so the directory can be incomplete with no indication.

Fix: Loop with startIndex/count until totalResults is reached, or at least log/raise when totalResults exceeds len(Resources).

Verification: The finding is real. In scim.py, `_list` (around lines 159-176) sends one GET to /api/scim/v2/{Users,Groups} with no startIndex or count parameters and returns `response.json().get("Resources") or []`. The code never reads `totalResults` or `itemsPerPage` anywhere in src/ (the only matches are in scim_push.py, which builds its own responses). RFC 7644 section 3.4.2.4 lets a service provider return fewer results than `totalResults`, so any page after the first is dropped without warning. `get_directory` then hands back an incomplete SCIMDirectory with no sign that anything is missing. Medium severity fits: this only causes trouble when SRAM actually pages the response, but the data loss would go unnoticed.

#### main.py duplicates the demo app's composition and auth routes

`src/sram_fastapi/main.py:0`, design

create_app duplicates create_demo_app's SessionMiddleware setup and dependency override verbatim, and re-implements /auth/login, /auth/callback, /auth/logout and /api/protected that the demo routers (routers/auth.py, pages.py) also define. Only tests import sram_fastapi.main (tests/conftest.py, tests/test_auth.py); the Makefile serves demo.app:get_demo_app. Two parallel implementations of the login callback (including the 4 KB cookie rule) will diverge.

Fix: Extract a shared library function (e.g. add_session_middleware(app, settings) and a library auth router) used by both apps, or remove main.py and point tests at the demo app.

Verification: I confirmed the finding by reading the code. In sram-fastapi/src/sram_fastapi/main.py, create_app sets up SessionMiddleware with the same arguments as create_demo_app in demo/app.py, and both apply the same get_settings dependency override. main.py also defines its own /auth/login, /auth/callback, /auth/logout and /api/protected routes. The demo already serves those paths through demo/routers/auth.py and demo/routers/pages.py (line 161). The two versions have already diverged. The demo callback wraps handle_callback in error handling that depends on the debug setting and calls warn_if_session_too_large. The callback in main.py has neither, but repeats the same comment about the 4 KB cookie limit. Only tests/conftest.py and tests/test_auth.py import sram_fastapi.main. The Makefile serves sram_fastapi.demo.app:get_demo_app. This means the tests exercise a callback that differs from the one the app actually serves. Medium severity is appropriate.

#### /api/hello reimplements bearer-token introspection instead of using library dependency

`src/sram_fastapi/demo/routers/pages.py:176`, design

hello() parses the Authorization header, introspects, and maps errors by hand, duplicating logic that belongs in the library (get_token_user exists for /api/protected). It also leaks upstream exception text (`detail=f"Token introspection failed: {e}"`, may include internal URLs) and returns the full raw introspection result to the caller. Exceptions are re-raised without `from e`.

Fix: Move SRAM-token introspection into a library dependency and use it here; return a sanitized 502 message.

Verification: The finding holds. In sram-fastapi/src/sram_fastapi/demo/routers/pages.py, hello() (lines 176-231) parses the Authorization header and maps errors to 401 by hand. That duplicates get_token_user in src/sram_fastapi/auth.py:273-296. The only real difference is that hello() calls introspect_sram_token rather than introspect_token. Because this SRAM-token path exists only in the demo router, the library has no reusable dependency for it, which conflicts with the CLAUDE.md rule that consumer applications hold configuration only. The httpx.HTTPError branch returns `detail=f"Token introspection failed: {e}"`, and httpx error text can include the upstream URL. Both HTTPException raises in the except blocks omit `from e`. The response sets `introspection=result`, which returns the raw introspection payload. That part may be intended for the demo's token-test page, so it is a weaker point than the duplication and the leaked error text. Medium severity is fair.

### Low

#### allowed_redirect_urls is never used

`src/sram_fastapi/config.py:74`, dead-code

`allowed_redirect_urls: list[str] = ["http://localhost:8124"]` is referenced nowhere in src or tests (grep). Its name suggests open-redirect protection, but nothing checks redirects against it.

Fix: Remove it, or connect it to the login/callback redirect validation and add a test.

Verification: A repo-wide grep finds allowed_redirect_urls in only two places: its definition at src/sram_fastapi/config.py:74 and a copy of that line in docs/api-reference.md:330. No code reads it and no test covers it, so this is dead configuration whose name suggests redirect validation that is never done. This breaks the rule in CLAUDE.md against unconnected code. Because nothing ever enforced the setting, it opens no new security hole. The concrete risk is that operators may believe it protects them. That makes it a low-severity dead-code finding.

#### Module-global OIDC client singleton captures the first settings

`src/sram_fastapi/auth.py:241`, design

`get_oidc_client` caches `_oidc_client` in a module global the first time it is called and ignores the `settings` passed on later calls. This conflicts with the rule to depend on injected interfaces rather than globals. Dependency overrides of get_settings have no effect after the first call, which is why the tests have to reset `_oidc_client` by hand.

Fix: Build the OIDCClient once in the app factory (app.state, or a closure provider) and inject it, rather than using a module global.

Verification: The core claim holds. In sram-fastapi/src/sram_fastapi/auth.py, lines 241-251 cache the OIDCClient in the module global `_oidc_client` the first time `get_oidc_client` is called. On later calls the injected `settings` is ignored. As a result, a dependency override of `get_settings`, or two apps with different settings in one process, will silently reuse the client built from the first settings. That breaks the injected-interface rule in CLAUDE.md. One part of the finding is wrong: no test resets `_oidc_client` by hand. tests/test_auth.py:203 and tests/test_demo.py:146 override `get_oidc_client` itself, which works around the problem. There is no evidence of a production failure, since one app uses one set of settings. I am lowering the severity to low, as a design and testability issue.

#### Push POST/PUT accept malformed bodies with a 500

`src/sram_fastapi/scim_push.py:148`, correctness

`store.put(kind, ..., await request.json())` - an invalid JSON body raises JSONDecodeError and a JSON array/string makes `{**body}` raise TypeError; both become 500 instead of a SCIM 400 error. Same at line 159.

Fix: Parse the body in a helper that returns `_error(400, ...)` unless it is a JSON object.

Verification: The finding is real, but I'd lower it to low severity. Lines 148 and 159 pass `await request.json()` straight to `store.put`, and nothing checks the body first. Starlette's `Request.json()` just calls `json.loads`, so a body that isn't valid JSON raises JSONDecodeError. No handler in the router catches it, so the client gets a 500. If the body is valid JSON but not an object (an array, string or number), `{**body}` in `SCIMStore.put` (line 50) raises TypeError, which also becomes a 500. Either way the client gets a 500 instead of the SCIM 400 error that `_error` would send. The impact is small: the caller has already passed bearer-token authentication, the normal caller is SRAM sending valid SCIM objects, the store is an in-memory proof of concept, and no data is corrupted because the exception happens before anything is stored. The real defect is the wrong status code for bad input from a trusted caller.

#### testRoute writes server strings into innerHTML unescaped (DOM XSS)

`src/sram_fastapi/demo/templates/base.html:201`, correctness

`resultDiv.innerHTML = '<span class="success">Access granted</span> - ' + data.message;` and the same with `data.detail` / `e.message`. Group route messages embed the collaboration/group path segments (authorization.py: f"Group check passed for {collaboration}/{group}") and AuthorizationError details embed group/entitlement names, all of which originate from SRAM entitlements or the URL. Any `<` in them is parsed as HTML. test_token.html already has an escapeHtml helper for exactly this; base.html does not use it.

Fix: Use textContent for the dynamic parts (build the span with createElement) or move escapeHtml into base.html and apply it to data.message, data.detail and e.message.

Verification: The finding holds, but the severity should be low rather than medium. In sram-fastapi/src/sram_fastapi/demo/templates/base.html, testRoute (around lines 201, 204 and 208) inserts data.message, data.detail and e.message into the page with innerHTML string concatenation and no escaping. One server message builds its text straight from path parameters: routers/authorization.py:129 returns `message=f"Group check passed for {collaboration}/{group}"`. The escaping flaw exists. It is hard to exploit, though. The URLs testRoute fetches come from server-rendered data-test-url attributes, not from anything the viewer types. The "passed" message only appears when the viewer's own SRAM entitlements contain the group, and those names are set by SRAM administrators. An attacker therefore cannot easily get markup into these responses. That leaves a missing-escaping defect in the demo UI rather than a medium-severity XSS. The suggested fix, using textContent or an escapeHtml helper, is still correct.

#### nav_is_manager discovers its collaborators instead of receiving them

`src/sram_fastapi/demo/templating.py:13`, design

`_nav_is_manager` lazily imports `sram_fastapi.demo.routers.collaborations.is_manager_user` (templating layer reaching into a router module, function-level import to dodge a cycle) and reads `request.app.state.settings` (a global reached via the request). This is the discovered-dependency pattern the project rules forbid; create_templates() takes no settings and cannot be tested with substituted ones.

Fix: Have create_templates(settings, is_manager) accept the settings and the manager predicate (or move is_manager_user to the library) and bind them in a closure; drop app.state.settings if nothing else needs it.

Verification: The finding is real. In /Users/sdrwacker/workspace/sram-demo/sram-fastapi/src/sram_fastapi/demo/templating.py, lines 13-17 define `_nav_is_manager`. It imports `is_manager_user` inside the function from `sram_fastapi.demo.routers.collaborations`. That import has to be deferred because collaborations.py (line 36) imports `create_templates` from templating, so importing at module level would create a cycle. The function also reads `request.app.state.settings`, which app.py sets at line 95. `create_templates()` takes no arguments, so a test cannot pass in different settings or a different manager check. The global CLAUDE.md forbids exactly this ("Depend on injected interfaces, never on discovered implementations... resolving a global"). I lowered the severity to low because this is demo-application code, it works as written, and the problem is maintainability, not a functional bug.

#### Reference app still identifies users by sub, not voperson_id

`src/sram_fastapi/main.py:59`, consistency

`f"Hello, {user.name or user.email or user.sub}!"`, `/auth/me` returns `"sub": user.sub` and `/api/protected` returns `"user": user.sub`. Since the EngineBlock migration users are identified by User.identifier (voperson_id); sub is an EngineBlock pairwise value. home.html line 33 has the same fallback (`user.preferred_username or user.sub`).

Fix: Use user.identifier in these responses/templates (and expose it in /auth/me).

Verification: I confirmed the finding in the code. In main.py, line 59 falls back to user.sub in the greeting, line 104 returns "sub": user.sub from /auth/me without exposing user.identifier, and line 118 returns "user": user.sub from /api/protected. The User.identifier property (auth module, around lines 87-97) says browser-login users are recognised by voperson_id and that their sub is unrelated. So the reference app identifies users by a value the library itself calls unrelated. The impact is limited: this is a demo or reference app, sub is still a real claim, and nothing here makes an authorization decision based on it. I rated it low because the issue is an inconsistency in a misleading example, not a functional bug. I did not verify the home.html part, because that template is not at src/sram_fastapi/templates/home.html.

#### State-changing forms carry no CSRF token

`src/sram_fastapi/demo/templates/collaboration_detail.html:49`, correctness

All POST forms (role change, member removal, invitations, groups, service connect/disconnect, collaboration delete, /collaborations/new) have no CSRF token, and no CSRF middleware exists in src (grep for csrf finds only the OAuth state comment). Protection rests solely on the session cookie's same_site="lax", which does not cover same-site subdomain attackers or older browsers. The project rules require CSRF on state-changing forms.

Fix: Add a per-session CSRF token (stored in the session, rendered as a hidden input, checked in a shared dependency on every POST route) or an Origin/Referer check, with a test that a POST without it is rejected.

Verification: Confirmed: the POST forms in sram-fastapi/src/sram_fastapi/demo/templates/collaboration_detail.html (lines 49, 54, 75, 102 and others) carry no CSRF token. The only CSRF mention in the codebase is the OAuth state comment in auth.py:145, and no CSRF or Origin-check middleware exists. SessionMiddleware is configured with same_site="lax" (main.py:38, demo/app.py:91). Modern browsers do not send Lax cookies on cross-site POSTs, which blocks classic CSRF. What remains is same-site sibling-subdomain attackers and legacy browsers. The finding's claim that "project rules require CSRF" is not supported: no rule mentioning CSRF exists in the repo or in CLAUDE.md. Because this is a demo app with a Lax mitigation already in place, I rate it low rather than medium.

#### Authorization helpers live in a router module and are imported by other layers

`src/sram_fastapi/demo/routers/collaborations.py:56`, design

is_manager_user and require_manager are defined in the demo router, yet templating.py (`from sram_fastapi.demo.routers.collaborations import is_manager_user`, done lazily inside a function) and routers/scim.py (`import require_manager`) reach into this router for them. Together with _is_member, _is_collaboration_admin and _require_access, this is shared authorization logic, which the project rules say belongs in the library, not in the demo app, and templating probing a router module is a hidden cross-layer dependency.

Fix: Move the manager/member/admin checks into the library (e.g. sram_fastapi.collaborations or auth) and import them from there in the routers and templating; add an import-boundary gate test.

Verification: The finding holds. I confirmed both imports in the code: - `demo/templating.py:15` imports `is_manager_user` from `demo/routers/collaborations.py`. The import sits inside `_nav_is_manager`, probably to avoid a circular import. - `demo/routers/scim.py:14` imports `require_manager` from the same router module. The entitlement checks (`is_manager_user` at line 56, `_is_member`, `_is_collaboration_admin`, `require_manager`) are plain logic over `User`, `Settings` and `Collaboration`. They would sit naturally in the library modules `sram_fastapi/auth.py` or `sram_fastapi/collaborations.py`. I'm lowering the severity to low. Every import stays inside the demo package, so no library module depends on the demo. This is a layering and cohesion problem: the template environment reaches into a router module. It is not a cross-package fork, and it causes no correctness bug.

#### Link to non-existent /demo/authorization route

`src/sram_fastapi/demo/templates/forbidden.html:66`, correctness

`<a href="/demo/authorization">Back to Authorization Demo</a>` points to a route no router defines (the /demo router only serves /entitlement-protected, /affiliation-protected, /groups/..., /features/...). Every 403 page has a link that returns 404.

Fix: Link to / (where the authorization tests live) or drop the button; add a test that resolves links in error templates.

Verification: The link `/demo/authorization` appears at line 66 of sram-fastapi/src/sram_fastapi/demo/templates/forbidden.html. No router defines that path. The `/demo` router in demo/routers/authorization.py serves only /entitlement-protected, /affiliation-protected, /groups/{c}/{g} and /features/{feature}. No other route definition (main.py, pages, auth, collaborations, scim) matches it. demo/app.py:110 renders forbidden.html for 403 responses, so clicking that button returns a 404. This is a broken navigation link in the UI with no security or data impact, so the severity should be low rather than medium.

#### Group denials show affiliation guidance

`src/sram_fastapi/demo/templates/forbidden.html:43`, correctness

require_group raises AuthorizationError with check_type="group", but the template only branches `{% if check_type == 'entitlement' %} ... {% else %}` affiliation text. A group denial tells the user to contact their institution's identity management team, which is wrong: groups come from SRAM collaboration membership.

Fix: Add an explicit 'group' branch (or treat group like entitlement) and a test rendering a group denial.

Verification: I confirmed the finding from the code. In src/sram_fastapi/auth.py, line 528 raises AuthorizationError with check_type="group". The exception handler in demo/app.py:116 passes exc.check_type to forbidden.html. That template (line 43) has a branch only for 'entitlement', and every other value falls into the else branch, which tells the user that affiliations come from their home institution and to contact their institution's identity management team. A group denial therefore shows the wrong guidance. A second value has the same problem: demo/routers/collaborations.py:397 raises with check_type="setting", which also gets the affiliation text. I lowered the severity to low because the wrong text appears only on the demo app's error page. Access control itself is not affected.

#### validate_token unauthenticated and unprotected against CSRF; malformed body yields 500

`src/sram_fastapi/demo/routers/pages.py:253`, correctness

POST /test-token/validate has no auth dependency, so anyone can use the service's introspection token as an oracle against SRAM. `await request.json()` on a non-JSON body raises JSONDecodeError (500), and `body.get("token", "").strip()` fails with AttributeError if body is not an object or token is not a string. `TokenValidationResponse(**result, validation_time_ms=...)` raises TypeError (500) if the introspection result itself contains validation_time_ms, and ValidationError if fields have unexpected types; the `except ValueError` is placed so it only catches pydantic validation errors, not config errors as its 'config-error' status claims.

Fix: Use a Pydantic request body model, require a logged-in user (or document why it is public), and name the error status honestly.

Verification: The core claims are confirmed in sram-fastapi/src/sram_fastapi/demo/routers/pages.py, lines 253-261. `validate_token` has no user dependency. It only injects `get_oidc_client` and `get_settings`, while other routes use `get_optional_user` or `get_token_user`. It reads the body with `body = await request.json()` and then calls `body.get("token", "").strip()`, with no Pydantic model. As a result, a body that is not valid JSON raises JSONDecodeError and returns a 500. A JSON array, or a token that is not a string, raises AttributeError and also returns a 500. Because the endpoint is public, anyone can use the service's introspection credential to check tokens against SRAM. The CSRF point adds little, since the response is only an oracle result. I did not verify the edge cases about `**result` and ValidationError, which depend on what the introspection call returns. I lowered the severity to low because this is a demo router, and the realistic impact is unhandled 500s on malformed input plus an unauthenticated introspection oracle.

## Remediation checklist

- [x] Refuse to start with the placeholder or a short `SECRET_KEY` (high). #40
- [x] `main.py` reuses the shared login routes in `sram_fastapi.auth_routes` and identifies users by `User.identifier`; a gate test refuses a second callback route. #42
- [x] SRAM application token validation is a library dependency, `require_sram_application_token`, used by `/api/hello`. `get_token_user` validates OIDC access tokens, a different mechanism, so it was not the right replacement. #43
- [x] SCIM pull follows pages (#41); malformed SCIM push bodies get 400 `invalidSyntax` (#46).
- [x] Test-button results are inserted as text (#44); cross-site `POST`/`PUT`/`PATCH`/`DELETE` requests are refused by `Origin` in addition to `SameSite=Lax` (#45). Logout stays a `GET`: a forged logout only ends the session.
- [x] The OIDC client, SCIM store and service group index are kept per application on `app.state`; the manager check is `is_collaboration_manager` in the library, and a gate test refuses imports from the collaborations router. #49
- [x] `allowed_redirect_urls` and the `/demo/authorization` link removed, denial guidance matches the check type (#47). `get_app` is used by the README's start command and stays.
- [x] vulture runs as a pre-commit hook at confidence 80; ruff and vulture are in the dev dependency group. #48

## Appendix: unverified low findings

| Location | Category | Finding |
|----------|----------|---------|
| `src/sram_fastapi/auth.py:108` | design | get_discovery_document duplicates authlib metadata loading |
| `src/sram_fastapi/auth.py:162` | correctness | introspect_token silently swallows all failures |
| `src/sram_fastapi/auth.py:296` | correctness | get_token_user raises KeyError when introspection result lacks sub |
| `src/sram_fastapi/auth.py:326` | typing | Bare Callable return types on authorization factories |
| `src/sram_fastapi/config.py:77` | typing | Unneeded forward-reference string annotation |
| `src/sram_fastapi/config.py:90` | correctness | Malformed feature group entries parse silently |
| `src/sram_fastapi/__init__.py:3` | consistency | __version__ hard-coded, duplicating pyproject version |
| `src/sram_fastapi/auth.py:100` | docstring | Incomplete Google-style docstrings on public API |
| `src/sram_fastapi/collaborations.py:437` | dead-code | list_members is never called outside tests |
| `src/sram_fastapi/collaborations.py:879` | design | Module-level singleton for ServiceGroupIndex |
| `src/sram_fastapi/collaborations.py:677` | correctness | update_group cannot clear the description, and an empty name is silently ignored |
| `src/sram_fastapi/collaborations.py:533` | consistency | Expiry date units differ between methods |
| `src/sram_fastapi/collaborations.py:66` | docstring | identifier_of docstring still describes the OIDC subject |
| `src/sram_fastapi/collaborations.py:130` | docstring | _segment docstring has prose after the Args section |
| `src/sram_fastapi/scim.py:176` | correctness | Non-JSON SCIM response escapes as unhandled ValueError |
| `src/sram_fastapi/scim_push.py:62` | design | Module-level store singleton reached by get_scim_store |
| `src/sram_fastapi/demo/templates/scim.html:64` | design | Template reassigns `directory` to pushed data and duplicates the user/group tables |
| `src/sram_fastapi/main.py:127` | dead-code | get_app is never referenced |
| `src/sram_fastapi/demo/templates/test_token.html:187` | correctness | Jinja values interpolated into a JavaScript template literal with HTML escaping only |
| `src/sram_fastapi/demo/templates/home.html:102` | consistency | Inline onclick handlers alongside data-test-url pattern |
| `src/sram_fastapi/demo/templates/test_token.html:420` | dead-code | Unused CSS rules |
| `src/sram_fastapi/demo/app.py:74` | docstring | Public factories lack Google-style Args/Returns sections |
| `src/sram_fastapi/demo/routers/collaborations.py:278` | dead-code | 'error' context key and template error blocks can never fire |
| `src/sram_fastapi/demo/routers/collaborations.py:652` | consistency | Unconfigured detail page duplicates the overview context |
| `src/sram_fastapi/demo/routers/collaborations.py:589` | correctness | Group member uid not checked against the collaboration's memberships |
| `src/sram_fastapi/demo/templates/collaboration_detail.html:139` | consistency | Group update form requires name though the route treats it as optional |
| `src/sram_fastapi/demo/routers/auth.py:73` | correctness | Debug branch leaks exception text and drops cause |
| `src/sram_fastapi/demo/routers/auth.py:84` | correctness | Logout via GET is CSRF-able |
| `src/sram_fastapi/demo/routers/authorization.py:24` | design | Deployment-specific authorization values hard-coded in demo code |
| `src/sram_fastapi/demo/routers/authorization.py:127` | design | Calls dependency factory imperatively inside handler |
| `src/sram_fastapi/demo/routers/pages.py:99` | typing | Missing return annotations and bare dict |

Refuted: src/sram_fastapi/demo/routers/pages.py:168 Protected API identifies user by sub, not voperson_id
