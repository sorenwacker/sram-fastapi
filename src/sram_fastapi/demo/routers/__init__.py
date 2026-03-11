"""Demo application routers."""

from sram_fastapi.demo.routers.auth import create_auth_router
from sram_fastapi.demo.routers.authorization import create_authorization_router
from sram_fastapi.demo.routers.pages import create_pages_router

__all__ = ["create_auth_router", "create_authorization_router", "create_pages_router"]
