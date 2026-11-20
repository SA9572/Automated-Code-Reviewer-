import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.database.base import Base
from app.database.models import AgentResultModel, FindingModel
from app.repositories.review_repository import ReviewRepository
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.schemas.review import (
    MergeRecommendation,
    Review,
    ReviewStatus,
)
from app.state.change_state import ChangeState
from app.state.state_manager import StateManager


@pytest_asyncio.fixture
async def db_session():
    """Create an isolated in-memory SQLite database for each test."""

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


# ============================================================
# TEST DATA HELPERS
# ============================================================


def make_finding():
    return Finding(
        agent_name="DiffAgent",
        title="Potential unsafe input handling",
        description="User input is not validated.",
        severity=Severity.HIGH,
        confidence=0.95,
        file_path="backend/app/main.py",
        start_line=10,
        end_line=12,
        evidence="Unsafe user input",
        recommendation="Validate user input.",
    )


def make_review():
    finding = make_finding()

    agent_result = AgentResult(
        agent_name="DiffAgent",
        status=AgentStatus.SUCCESS,
        summary="Analysis completed.",
        findings=[finding],
        execution_time_ms=100,
    )

    return Review(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        status=ReviewStatus.COMPLETED,
        agent_results={"DiffAgent": agent_result},
        findings=[finding],
        summary="One high-severity issue found.",
        recommendation=MergeRecommendation.REQUEST_CHANGES,
    )


def make_change_state():
    return ChangeState(
        review_id=str(uuid4()),
        repository="test/state-repository",
        pull_request_number=10,
        base_commit="base123",
        head_commit="head456",
        changed_files=["backend/app/main.py"],
        diff_content="+password = 'hardcoded-secret'",
        change_summary="Added a potentially unsafe password.",
        agent_results={},
        final_findings=[],
        recommendation=MergeRecommendation.REVIEW_REQUIRED,
        status=ReviewStatus.PENDING,
    )


# ============================================================
# ORIGINAL REVIEW REPOSITORY TESTS (6)
# ============================================================


@pytest.mark.asyncio
async def test_create_and_retrieve_review(db_session):
    repository = ReviewRepository(db_session)
    review = make_review()

    await repository.create(review)

    stored = await repository.get_by_id(review.review_id)

    assert stored is not None
    assert stored.repository == "test/repository"
    assert stored.status == "COMPLETED"
    assert len(stored.agent_results) == 1
    assert len(stored.findings) == 1


@pytest.mark.asyncio
async def test_agent_and_final_findings_are_preserved(db_session):
    repository = ReviewRepository(db_session)
    review = make_review()

    await repository.create(review)

    result = await db_session.execute(
        select(FindingModel).where(FindingModel.review_id == review.review_id)
    )

    findings = result.scalars().all()

    assert len(findings) == 2

    agent_findings = [finding for finding in findings if not finding.is_final]

    final_findings = [finding for finding in findings if finding.is_final]

    assert len(agent_findings) == 1
    assert len(final_findings) == 1

    assert agent_findings[0].source_finding_id == final_findings[0].source_finding_id

    assert agent_findings[0].finding_id != final_findings[0].finding_id


@pytest.mark.asyncio
async def test_update_review_status(db_session):
    repository = ReviewRepository(db_session)
    review = make_review()

    await repository.create(review)

    updated = await repository.update_status(
        review.review_id,
        "PARTIAL",
    )

    assert updated is not None
    assert updated.status == "PARTIAL"


@pytest.mark.asyncio
async def test_delete_review(db_session):
    repository = ReviewRepository(db_session)
    review = make_review()

    await repository.create(review)

    deleted = await repository.delete(review.review_id)

    assert deleted is True

    stored = await repository.get_by_id(review.review_id)

    assert stored is None


@pytest.mark.asyncio
async def test_get_nonexistent_review(db_session):
    repository = ReviewRepository(db_session)

    result = await repository.get_by_id("does-not-exist")

    assert result is None


@pytest.mark.asyncio
async def test_delete_nonexistent_review(db_session):
    repository = ReviewRepository(db_session)

    result = await repository.delete("does-not-exist")

    assert result is False


# ============================================================
# INCREMENTAL CHANGESTATE PERSISTENCE TESTS (5)
# ============================================================


@pytest.mark.asyncio
async def test_create_and_retrieve_change_state(db_session):
    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    stored = await repository.get_state(state.review_id)

    assert stored is not None
    assert stored.review_id == state.review_id
    assert stored.repository == state.repository
    assert stored.pull_request_number == 10
    assert stored.base_commit == "base123"
    assert stored.head_commit == "head456"
    assert stored.changed_files == ["backend/app/main.py"]
    assert stored.diff_content == "+password = 'hardcoded-secret'"
    assert stored.status == ReviewStatus.PENDING


@pytest.mark.asyncio
async def test_update_and_retrieve_agent_result(db_session):
    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    agent_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Found a hardcoded password.",
        findings=[make_finding()],
        execution_time_ms=250,
    )

    await repository.update_agent_result(
        state.review_id,
        agent_result,
    )

    stored = await repository.get_state(state.review_id)

    assert stored is not None
    assert "SecurityAgent" in stored.agent_results

    result = stored.agent_results["SecurityAgent"]

    assert result.status == AgentStatus.SUCCESS
    assert result.summary == "Found a hardcoded password."
    assert result.execution_time_ms == 250
    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_update_final_review(db_session):
    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    finding = make_finding()

    await repository.update_final_review(
        review_id=state.review_id,
        findings=[finding],
        recommendation=MergeRecommendation.REQUEST_CHANGES,
        summary="One high-severity issue found.",
    )

    stored = await repository.get_state(state.review_id)

    assert stored is not None
    assert len(stored.final_findings) == 1

    assert stored.final_findings[0].title == ("Potential unsafe input handling")

    assert stored.recommendation == MergeRecommendation.REQUEST_CHANGES

    assert stored.status == ReviewStatus.COMPLETED
    assert stored.completed_at is not None


@pytest.mark.asyncio
async def test_update_state_status(db_session):
    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    await repository.update_state_status(
        state.review_id,
        ReviewStatus.PARTIAL,
    )

    stored = await repository.get_state(state.review_id)

    assert stored is not None
    assert stored.status == ReviewStatus.PARTIAL


@pytest.mark.asyncio
async def test_get_nonexistent_change_state(db_session):
    repository = ReviewRepository(db_session)

    with pytest.raises(
        KeyError,
        match="Review not found: nonexistent-review-id",
    ):
        await repository.get_state("nonexistent-review-id")


# ============================================================
# DUPLICATE UPDATE AND CONCURRENCY TESTS
# ============================================================


@pytest.mark.asyncio
async def test_duplicate_agent_update_does_not_create_duplicate_records(
    db_session,
):
    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    first_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="First analysis.",
        findings=[],
        execution_time_ms=100,
    )

    second_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Updated analysis.",
        findings=[],
        execution_time_ms=200,
    )

    await repository.update_agent_result(
        state.review_id,
        first_result,
    )

    await repository.update_agent_result(
        state.review_id,
        second_result,
    )

    query = select(AgentResultModel).where(
        AgentResultModel.review_id == state.review_id,
        AgentResultModel.agent_name == "SecurityAgent",
    )

    result = await db_session.execute(query)
    records = result.scalars().all()

    assert len(records) == 1


@pytest.mark.asyncio
async def test_latest_agent_update_is_preserved(db_session):
    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    first_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Old analysis.",
        findings=[],
        execution_time_ms=100,
    )

    latest_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Latest analysis.",
        findings=[],
        execution_time_ms=300,
    )

    await repository.update_agent_result(
        state.review_id,
        first_result,
    )

    await repository.update_agent_result(
        state.review_id,
        latest_result,
    )

    stored = await repository.get_state(state.review_id)

    assert stored.agent_results["SecurityAgent"].summary == ("Latest analysis.")
    assert stored.agent_results["SecurityAgent"].execution_time_ms == 300


@pytest.mark.asyncio
async def test_concurrent_agent_updates_are_preserved(db_session):
    session_factory = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
    )

    state_manager = StateManager(
        session_factory=session_factory,
    )

    state = make_change_state()
    await state_manager.create_state(state)

    diff_result = AgentResult(
        agent_name="DiffAgent",
        status=AgentStatus.SUCCESS,
        summary="Diff analysis completed.",
        findings=[],
        execution_time_ms=100,
    )

    security_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Security analysis completed.",
        findings=[],
        execution_time_ms=150,
    )

    await asyncio.gather(
        state_manager.update_agent_result(
            state.review_id,
            diff_result,
        ),
        state_manager.update_agent_result(
            state.review_id,
            security_result,
        ),
    )

    stored = await state_manager.get_state(state.review_id)

    assert "DiffAgent" in stored.agent_results
    assert "SecurityAgent" in stored.agent_results
    assert len(stored.agent_results) == 2


@pytest_asyncio.fixture
async def independent_session_factory(tmp_path):
    """Create a file-based SQLite database for independent sessions."""

    database_path = tmp_path / "concurrency_test.db"

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        echo=False,
        connect_args={"timeout": 30},
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )

    yield factory

    await engine.dispose()


@pytest.mark.asyncio
async def test_independent_managers_preserve_different_agent_updates(
    independent_session_factory,
):
    """Different managers must preserve both agents' results."""

    manager_one = StateManager(
        session_factory=independent_session_factory,
    )
    manager_two = StateManager(
        session_factory=independent_session_factory,
    )

    state = make_change_state()
    await manager_one.create_state(state)

    diff_result = AgentResult(
        agent_name="DiffAgent",
        status=AgentStatus.SUCCESS,
        summary="Diff analysis completed.",
        findings=[],
        execution_time_ms=100,
    )

    security_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Security analysis completed.",
        findings=[],
        execution_time_ms=150,
    )

    await asyncio.gather(
        manager_one.update_agent_result(
            state.review_id,
            diff_result,
        ),
        manager_two.update_agent_result(
            state.review_id,
            security_result,
        ),
    )

    stored = await manager_one.get_state(state.review_id)

    assert "DiffAgent" in stored.agent_results
    assert "SecurityAgent" in stored.agent_results
    assert len(stored.agent_results) == 2


@pytest.mark.asyncio
async def test_independent_managers_do_not_duplicate_same_agent(
    independent_session_factory,
):
    """Concurrent updates must not create duplicate agent records."""

    manager_one = StateManager(
        session_factory=independent_session_factory,
    )
    manager_two = StateManager(
        session_factory=independent_session_factory,
    )

    state = make_change_state()
    await manager_one.create_state(state)

    result_one = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="First security analysis.",
        findings=[],
        execution_time_ms=100,
    )

    result_two = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Second security analysis.",
        findings=[],
        execution_time_ms=200,
    )

    await asyncio.gather(
        manager_one.update_agent_result(
            state.review_id,
            result_one,
        ),
        manager_two.update_agent_result(
            state.review_id,
            result_two,
        ),
    )

    async with independent_session_factory() as session:
        query = select(AgentResultModel).where(
            AgentResultModel.review_id == state.review_id,
            AgentResultModel.agent_name == "SecurityAgent",
        )

        result = await session.execute(query)
        records = result.scalars().all()

    assert len(records) == 1
    assert records[0].summary in {
        "First security analysis.",
        "Second security analysis.",
    }


@pytest.mark.asyncio
async def test_agent_update_rolls_back_when_finding_persistence_fails(
    db_session,
    monkeypatch,
):
    """A failed agent update must not leave partial database changes."""

    repository = ReviewRepository(db_session)
    state = make_change_state()

    await repository.create_state(state)

    agent_result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Security analysis completed.",
        findings=[make_finding()],
        execution_time_ms=150,
    )

    def simulate_finding_failure(*args, **kwargs):
        raise RuntimeError("Simulated finding persistence failure")

    monkeypatch.setattr(
        repository,
        "_build_finding",
        simulate_finding_failure,
    )

    with pytest.raises(
        RuntimeError,
        match="Simulated finding persistence failure",
    ):
        await repository.update_agent_result(
            state.review_id,
            agent_result,
        )

    # Verify that no partial agent result was committed.
    query = select(AgentResultModel).where(
        AgentResultModel.review_id == state.review_id,
        AgentResultModel.agent_name == "SecurityAgent",
    )

    result = await db_session.execute(query)
    records = result.scalars().all()

    assert len(records) == 0

    # Verify that the original review state remains unchanged.
    stored = await repository.get_state(state.review_id)

    assert stored.status == ReviewStatus.PENDING
    assert "SecurityAgent" not in stored.agent_results


# ============================================================
# SQLITE TRANSIENT LOCK RETRY TESTS
# ============================================================


class FakeSQLiteSession:
    """Minimal async session used to test retry behavior."""

    def __init__(self):
        self.rollback_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def rollback(self):
        self.rollback_count += 1


def make_locked_error():
    return OperationalError(
        "UPDATE reviews SET status = ?",
        {},
        Exception("database is locked"),
    )


@pytest.mark.asyncio
async def test_sqlite_lock_error_is_retried_and_eventually_succeeds(
    monkeypatch,
):
    """Transient lock errors should retry and eventually succeed."""

    import app.state.state_manager as state_manager_module

    sessions = []

    def session_factory():
        session = FakeSQLiteSession()
        sessions.append(session)
        return session

    manager = StateManager(session_factory=session_factory)

    monkeypatch.setattr(
        state_manager_module,
        "SQLITE_RETRY_BASE_DELAY_SECONDS",
        0,
    )

    attempts = 0

    async def operation(repository):
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise make_locked_error()

        return "success"

    result = await manager._run_with_sqlite_retry(operation)

    assert result == "success"
    assert attempts == 3
    assert len(sessions) == 3
    # The first two failed attempts must roll back; the successful
    # third attempt must not roll back.
    assert [session.rollback_count for session in sessions] == [1, 1, 0]


@pytest.mark.asyncio
async def test_sqlite_lock_error_stops_after_maximum_attempts(
    monkeypatch,
):
    """Persistent lock errors must stop after the configured limit."""

    import app.state.state_manager as state_manager_module

    sessions = []

    def session_factory():
        session = FakeSQLiteSession()
        sessions.append(session)
        return session

    manager = StateManager(session_factory=session_factory)

    monkeypatch.setattr(
        state_manager_module,
        "SQLITE_RETRY_BASE_DELAY_SECONDS",
        0,
    )

    attempts = 0

    async def operation(repository):
        nonlocal attempts
        attempts += 1
        raise make_locked_error()

    with pytest.raises(OperationalError):
        await manager._run_with_sqlite_retry(operation)

    assert attempts == state_manager_module.SQLITE_MAX_ATTEMPTS
    assert len(sessions) == state_manager_module.SQLITE_MAX_ATTEMPTS
    assert all(session.rollback_count == 1 for session in sessions)


@pytest.mark.asyncio
async def test_non_lock_operational_error_is_not_retried():
    """Unrelated database errors must propagate immediately."""

    sessions = []

    def session_factory():
        session = FakeSQLiteSession()
        sessions.append(session)
        return session

    manager = StateManager(session_factory=session_factory)

    attempts = 0

    async def operation(repository):
        nonlocal attempts
        attempts += 1

        raise OperationalError(
            "SELECT * FROM reviews",
            {},
            Exception("disk I/O error"),
        )

    with pytest.raises(OperationalError, match="disk I/O error"):
        await manager._run_with_sqlite_retry(operation)

    assert attempts == 1
    assert len(sessions) == 1
    assert sessions[0].rollback_count == 1


@pytest.mark.asyncio
async def test_in_memory_state_manager_does_not_use_database_retries():
    """In-memory state management should continue working independently."""

    manager = StateManager()
    state = make_change_state()

    created = await manager.create_state(state)
    retrieved = await manager.get_state(state.review_id)

    assert created.review_id == state.review_id
    assert retrieved.review_id == state.review_id
    assert retrieved.status == ReviewStatus.PENDING


@pytest.mark.asyncio
async def test_unicode_text_is_preserved_after_database_round_trip(db_session):
    """Unicode finding text must survive SQLite persistence and retrieval."""

    repository = ReviewRepository(db_session)
    review = make_review()

    unicode_title = "Hardcoded credentials — café — नमस्ते — 中文"
    unicode_description = (
        "User-controlled input: naïve validation; Unicode characters: é, ñ, नमस्ते."
    )
    unicode_evidence = "Evidence: password = '秘密'; résumé — परीक्षण"
    unicode_recommendation = "Validate input correctly — सुरक्षित समाधान."

    finding = review.findings[0]
    finding.title = unicode_title
    finding.description = unicode_description
    finding.evidence = unicode_evidence
    finding.recommendation = unicode_recommendation

    await repository.create(review)

    stored = await repository.get_by_id(review.review_id)

    assert stored is not None
    assert len(stored.findings) == 1

    stored_finding = stored.findings[0]

    assert stored_finding.title == unicode_title
    assert stored_finding.description == unicode_description
    assert stored_finding.evidence == unicode_evidence
    assert stored_finding.recommendation == unicode_recommendation
