import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from test_review_repository import make_review

from app.database.base import Base
from app.database.models import (
    AgentResultModel,
    FindingModel,
    ReviewModel,
)
from app.repositories.review_repository import ReviewRepository


@pytest_asyncio.fixture
async def cascade_session(tmp_path):
    """Create an isolated SQLite database with FK enforcement enabled."""

    database_path = tmp_path / "cascade_test.db"

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        echo=False,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


async def count_records(session, model):
    result = await session.execute(select(func.count()).select_from(model))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_deleting_review_cascades_to_all_children(cascade_session):
    """Deleting a review must remove its agents and every finding."""

    repository = ReviewRepository(cascade_session)
    review = make_review()

    await repository.create(review)

    assert await count_records(cascade_session, ReviewModel) == 1
    assert await count_records(cascade_session, AgentResultModel) == 1
    assert await count_records(cascade_session, FindingModel) == 2

    # Use SQL DELETE to verify database-level cascade behavior.
    await cascade_session.execute(
        delete(ReviewModel).where(ReviewModel.review_id == str(review.review_id))
    )
    await cascade_session.commit()

    assert await count_records(cascade_session, ReviewModel) == 0
    assert await count_records(cascade_session, AgentResultModel) == 0
    assert await count_records(cascade_session, FindingModel) == 0


@pytest.mark.asyncio
async def test_deleting_agent_result_cascades_to_its_findings(
    cascade_session,
):
    """Deleting an agent must preserve its review and final findings."""

    repository = ReviewRepository(cascade_session)
    review = make_review()

    await repository.create(review)

    agent_result = await cascade_session.scalar(
        select(AgentResultModel).where(
            AgentResultModel.review_id == str(review.review_id)
        )
    )

    assert agent_result is not None

    # The review has one agent finding and one final finding.
    assert await count_records(cascade_session, FindingModel) == 2

    await cascade_session.execute(
        delete(AgentResultModel).where(AgentResultModel.id == agent_result.id)
    )
    await cascade_session.commit()

    assert await count_records(cascade_session, ReviewModel) == 1
    assert await count_records(cascade_session, AgentResultModel) == 0

    remaining_findings = (
        (
            await cascade_session.execute(
                select(FindingModel).where(
                    FindingModel.review_id == str(review.review_id)
                )
            )
        )
        .scalars()
        .all()
    )

    # The agent finding is deleted, but the final finding remains.
    assert len(remaining_findings) == 1
    assert remaining_findings[0].is_final is True
    assert remaining_findings[0].agent_result_id is None


@pytest.mark.asyncio
async def test_deleting_one_finding_preserves_unrelated_findings(
    cascade_session,
):
    """Deleting one finding must not affect other review findings."""

    repository = ReviewRepository(cascade_session)
    review = make_review()

    await repository.create(review)

    findings = (
        (
            await cascade_session.execute(
                select(FindingModel).where(
                    FindingModel.review_id == str(review.review_id)
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(findings) == 2

    finding_to_delete = findings[0]
    deleted_finding_id = finding_to_delete.finding_id

    await cascade_session.execute(
        delete(FindingModel).where(FindingModel.finding_id == deleted_finding_id)
    )
    await cascade_session.commit()

    assert await count_records(cascade_session, ReviewModel) == 1
    assert await count_records(cascade_session, AgentResultModel) == 1
    assert await count_records(cascade_session, FindingModel) == 1

    remaining = await cascade_session.scalar(
        select(FindingModel).where(FindingModel.finding_id == findings[1].finding_id)
    )

    assert remaining is not None
