"""Router module exports.

All API routers are imported here for convenient access from main.py.
"""

from app.routers.health import router as health_router
from app.routers.optimize import router as optimize_router
from app.routers.history import router as history_router
from app.routers.github_auth import router as github_auth_router
from app.routers.github_repos import router as github_repos_router
from app.routers.providers import router as providers_router
from app.routers.github import router as github_router
from app.routers.settings import router as settings_router

__all__ = [
    "health_router",
    "optimize_router",
    "history_router",
    "github_auth_router",
    "github_repos_router",
    "providers_router",
    "github_router",
    "settings_router",
]
