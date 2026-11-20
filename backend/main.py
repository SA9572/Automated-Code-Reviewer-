
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.dependencies import close_github_client, close_review_pipeline
from app.api.routes import github_router, health_router, reviews_router
from app.config import get_settings
from app.database.connection import close_db, init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup and close them on shutdown."""

    await init_db()

    try:
        yield

    finally:
        try:
            await close_github_client()
        finally:
            try:
                await close_review_pipeline()
            finally:
                await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-agent AI-powered automated code review system.",
    version="1.0.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.include_router(health_router, prefix="/api/v1")
app.include_router(reviews_router, prefix="/api/v1")
app.include_router(github_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "message": "Automated Code Review Board API",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
