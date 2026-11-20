
from functools import lru_cache

from app.database.connection import AsyncSessionLocal
from app.services.github_client import GitHubClient
from app.orchestration.review_pipeline import ReviewPipeline
from app.state.state_manager import StateManager


@lru_cache
def get_review_pipeline() -> ReviewPipeline:
    """Return a shared pipeline backed by persistent SQLite storage."""

    state_manager = StateManager(
        session_factory=AsyncSessionLocal,
    )

    return ReviewPipeline(state_manager=state_manager)


async def close_review_pipeline() -> None:
    """Close the cached review pipeline during application shutdown."""

    try:
        if get_review_pipeline.cache_info().currsize:
            pipeline = get_review_pipeline()
            await pipeline.close()

    finally:
        get_review_pipeline.cache_clear()



@lru_cache
def get_github_client() -> GitHubClient:
    """Return the shared server-side GitHub API client."""
    return GitHubClient()


async def close_github_client() -> None:
    """Close the shared GitHub HTTP client during application shutdown."""
    try:
        if get_github_client.cache_info().currsize:
            await get_github_client().close()
    finally:
        get_github_client.cache_clear()
