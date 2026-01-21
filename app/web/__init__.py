"""Web portal for the Community Mesh Platform."""

from .routes import router
from .api import api_router

__all__ = ["router", "api_router"]
