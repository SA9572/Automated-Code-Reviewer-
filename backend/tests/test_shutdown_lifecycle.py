"""FastAPI shutdown lifecycle tests for Phase 3."""

from unittest.mock import AsyncMock

import pytest

import app.api.dependencies as dependencies
import main


@pytest.fixture(autouse=True)
def clear_pipeline_dependency_cache():
    dependencies.get_review_pipeline.cache_clear()
    yield
    dependencies.get_review_pipeline.cache_clear()


@pytest.mark.asyncio
async def test_close_review_pipeline_does_not_create_uncached_pipeline():
    # An empty cache must not instantiate a new pipeline just to close it.
    assert dependencies.get_review_pipeline.cache_info().currsize == 0
    await dependencies.close_review_pipeline()
    assert dependencies.get_review_pipeline.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_close_review_pipeline_closes_cached_instance_and_clears_cache(monkeypatch):
    class FakePipeline:
        close = AsyncMock()

    pipeline = FakePipeline()
    monkeypatch.setattr(dependencies, "ReviewPipeline", lambda state_manager: pipeline)
    assert dependencies.get_review_pipeline() is pipeline
    close = AsyncMock()
    monkeypatch.setattr(pipeline, "close", close)

    await dependencies.close_review_pipeline()

    close.assert_awaited_once()
    assert dependencies.get_review_pipeline.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_close_review_pipeline_clears_cache_when_close_raises(monkeypatch):
    class FakePipeline:
        close = AsyncMock(side_effect=RuntimeError("shutdown close failed"))

    pipeline = FakePipeline()
    monkeypatch.setattr(dependencies, "ReviewPipeline", lambda state_manager: pipeline)
    assert dependencies.get_review_pipeline() is pipeline
    close = pipeline.close

    with pytest.raises(RuntimeError, match="shutdown close failed"):
        await dependencies.close_review_pipeline()

    close.assert_awaited_once()
    assert dependencies.get_review_pipeline.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_fastapi_lifespan_shutdown_order(monkeypatch):
    events = []

    async def init_db():
        events.append("init_db")

    async def close_pipeline():
        events.append("close_pipeline")

    async def close_db():
        events.append("close_db")

    monkeypatch.setattr(main, "init_db", init_db)
    monkeypatch.setattr(main, "close_review_pipeline", close_pipeline)
    monkeypatch.setattr(main, "close_db", close_db)

    async with main.lifespan(main.app):
        events.append("application_running")

    assert events == ["init_db", "application_running", "close_pipeline", "close_db"]


@pytest.mark.asyncio
async def test_fastapi_lifespan_closes_database_if_pipeline_cleanup_fails(monkeypatch):
    events = []

    async def init_db():
        events.append("init_db")

    async def close_pipeline():
        events.append("close_pipeline")
        raise RuntimeError("pipeline cleanup failed")

    async def close_db():
        events.append("close_db")

    monkeypatch.setattr(main, "init_db", init_db)
    monkeypatch.setattr(main, "close_review_pipeline", close_pipeline)
    monkeypatch.setattr(main, "close_db", close_db)

    with pytest.raises(RuntimeError, match="pipeline cleanup failed"):
        async with main.lifespan(main.app):
            events.append("application_running")

    assert events == ["init_db", "application_running", "close_pipeline", "close_db"]
