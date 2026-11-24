"""Demo application routers."""

from sram_fastapi.demo.routers.authorization import create_authorization_router
from sram_fastapi.demo.routers.collaborations import create_collaborations_router
from sram_fastapi.demo.routers.pages import create_pages_router
from sram_fastapi.demo.routers.scim import create_scim_router

__all__ = [
    "create_authorization_router",
    "create_collaborations_router",
    "create_pages_router",
    "create_scim_router",
]
