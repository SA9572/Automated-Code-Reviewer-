from app.api.routes.github import router as github_router
from app.api.routes.health import router as health_router
from app.api.routes.reviews import router as reviews_router

__all__ = [
    "github_router",
    "health_router",
    "reviews_router",
]
