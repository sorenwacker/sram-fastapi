"""SCIM push endpoints that SRAM provisions users and groups into.

SRAM sends a request for every change in a connected collaboration, and optionally
a periodic full push (sweep). Resources are kept in process memory as a proof of
concept; a restart empties the store and the next sweep fills it again.
"""

import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from sram_fastapi.config import Settings, get_settings

ResourceType = Literal["Users", "Groups"]

ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_MEDIA_TYPE = "application/scim+json"
EXTERNAL_ID_FILTER = re.compile(r'^\s*externalId\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE)


class SCIMStore:
    """In-memory store of pushed SCIM resources, keyed by type and id."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._resources: dict[str, dict[str, dict[str, Any]]] = {"Users": {}, "Groups": {}}

    def list(self, kind: ResourceType, external_id: str | None = None) -> list[dict[str, Any]]:
        """Return stored resources, optionally only those with the given externalId."""
        resources = list(self._resources[kind].values())
        if external_id is None:
            return resources
        return [r for r in resources if r.get("externalId") == external_id]

    def get(self, kind: ResourceType, resource_id: str) -> dict[str, Any] | None:
        """Return one resource, or None."""
        return self._resources[kind].get(resource_id)

    def put(self, kind: ResourceType, resource_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Store a resource under the given id, stamping id and meta."""
        now = datetime.now(UTC).isoformat()
        created = (self._resources[kind].get(resource_id) or {}).get("meta", {}).get("created", now)
        resource = {
            **body,
            "id": resource_id,
            "meta": {"resourceType": kind[:-1], "created": created, "lastModified": now},
        }
        self._resources[kind][resource_id] = resource
        return resource

    def delete(self, kind: ResourceType, resource_id: str) -> bool:
        """Remove a resource; return whether it existed."""
        return self._resources[kind].pop(resource_id, None) is not None


def get_scim_store(request: Request) -> SCIMStore:
    """Return the application's SCIM push store, creating it on first use.

    Args:
        request: The current request, whose application holds the store.

    Returns:
        The store shared by every request to this application.
    """
    store = getattr(request.app.state, "scim_push_store", None)
    if store is None:
        store = SCIMStore()
        request.app.state.scim_push_store = store
    return store


Store = Annotated[SCIMStore, Depends(get_scim_store)]
Config = Annotated[Settings, Depends(get_settings)]


async def _json_object(request: Request) -> dict[str, Any] | None:
    """Return the request body as a JSON object, or None when it is not one."""
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error(status: int, detail: str, scim_type: str | None = None) -> JSONResponse:
    """A SCIM error response."""
    content: dict[str, Any] = {"schemas": [ERROR_SCHEMA], "status": str(status), "detail": detail}
    if scim_type:
        content["scimType"] = scim_type
    return JSONResponse(
        content,
        status_code=status,
        media_type=SCIM_MEDIA_TYPE,
    )


def _scim(body: dict[str, Any], status: int = 200) -> JSONResponse:
    """A SCIM JSON response."""
    return JSONResponse(body, status_code=status, media_type=SCIM_MEDIA_TYPE)


def _authorised(request: Request, settings: Settings) -> JSONResponse | None:
    """Return an error response unless the request carries the push token."""
    token = settings.sram_scim_push_token
    if not token:
        return _error(404, "SCIM push is not enabled")
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, token):
        return _error(401, "Invalid or missing bearer token")
    return None


def create_scim_push_router() -> APIRouter:
    """Create the router serving SCIM push endpoints under /scim/v2.

    Returns:
        APIRouter for Users and Groups.
    """
    router = APIRouter(prefix="/scim/v2", tags=["scim-push"])

    @router.get("/{kind}")
    async def list_resources(
        kind: ResourceType, request: Request, store: Store, settings: Config
    ) -> Response:
        """List stored resources, optionally filtered by externalId."""
        if denied := _authorised(request, settings):
            return denied
        external_id = None
        if (expression := request.query_params.get("filter")) is not None:
            match = EXTERNAL_ID_FILTER.match(expression)
            if not match:
                return _error(400, "Only 'externalId eq \"...\"' filters are supported")
            external_id = match.group(1)
        resources = store.list(kind, external_id)
        return _scim(
            {
                "schemas": [LIST_SCHEMA],
                "totalResults": len(resources),
                "startIndex": 1,
                "itemsPerPage": len(resources),
                "Resources": resources,
            }
        )

    @router.get("/{kind}/{resource_id}")
    async def get_resource(
        kind: ResourceType, resource_id: str, request: Request, store: Store, settings: Config
    ) -> Response:
        """Return one stored resource."""
        if denied := _authorised(request, settings):
            return denied
        resource = store.get(kind, resource_id)
        return _scim(resource) if resource else _error(404, f"{kind} {resource_id} not found")

    @router.post("/{kind}")
    async def create_resource(
        kind: ResourceType, request: Request, store: Store, settings: Config
    ) -> Response:
        """Store a new resource under a newly assigned id."""
        if denied := _authorised(request, settings):
            return denied
        body = await _json_object(request)
        if body is None:
            return _error(400, "Body must be a JSON object", scim_type="invalidSyntax")
        return _scim(store.put(kind, uuid.uuid4().hex, body), status=201)

    @router.put("/{kind}/{resource_id}")
    async def replace_resource(
        kind: ResourceType, resource_id: str, request: Request, store: Store, settings: Config
    ) -> Response:
        """Replace a stored resource."""
        if denied := _authorised(request, settings):
            return denied
        if store.get(kind, resource_id) is None:
            return _error(404, f"{kind} {resource_id} not found")
        body = await _json_object(request)
        if body is None:
            return _error(400, "Body must be a JSON object", scim_type="invalidSyntax")
        return _scim(store.put(kind, resource_id, body))

    @router.delete("/{kind}/{resource_id}")
    async def delete_resource(
        kind: ResourceType, resource_id: str, request: Request, store: Store, settings: Config
    ) -> Response:
        """Remove a stored resource."""
        if denied := _authorised(request, settings):
            return denied
        if not store.delete(kind, resource_id):
            return _error(404, f"{kind} {resource_id} not found")
        return Response(status_code=204)

    return router
