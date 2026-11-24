"""Template environment shared by all demo pages."""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from sram_fastapi.auth import User, is_collaboration_manager

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _nav_is_manager(request: Request, user: User | None) -> bool:
    """Whether the navigation should offer manager-only pages to this user."""
    return user is not None and is_collaboration_manager(user, request.app.state.settings)


def create_templates() -> Jinja2Templates:
    """Create the template environment used by every demo page.

    Returns:
        Templates with ``nav_is_manager(request, user)`` available to the base layout.
    """
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.globals["nav_is_manager"] = _nav_is_manager
    return templates
