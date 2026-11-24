"""Template environment shared by all demo pages."""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import User

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _nav_is_manager(request: Request, user: User | None) -> bool:
    """Whether the navigation should offer manager-only pages to this user."""
    from sram_fastapi.demo.routers.collaborations import is_manager_user

    return user is not None and is_manager_user(user, request.app.state.settings)


def create_templates() -> Jinja2Templates:
    """Create the template environment used by every demo page.

    Returns:
        Templates with ``nav_is_manager(request, user)`` available to the base layout.
    """
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.globals["nav_is_manager"] = _nav_is_manager
    return templates
