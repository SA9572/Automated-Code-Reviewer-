import pytest

from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.schemas.review import MergeRecommendation, ReviewStatus
from app.state.change_state import ChangeState
from app.state.state_manager import StateManager


def create_state():

    return ChangeState(
        repository="example/project",
        pull_request_number=42,
        base_commit="abc123",
        head_commit="def456",
        changed_files=["main.py", "config.py"],
        diff_content="Example code diff",
    )


def create_agent_result():

    return AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Security analysis completed.",
        execution_time_ms=1200,
    )


def create_finding():

    return Finding(
        agent_name="SecurityAgent",
        title="Hardcoded secret",
        description="A secret was detected.",
        severity=Severity.CRITICAL,
        confidence=0.99,
        file_path="config.py",
        start_line=10,
        evidence='API_KEY = "example-secret"',
        recommendation="Use environment variables.",
    )


def test_change_state_creation():

    state = create_state()

    assert state.repository == "example/project"
    assert state.pull_request_number == 42
    assert len(state.changed_files) == 2
    assert state.status == ReviewStatus.PENDING
    assert state.total_findings == 0


@pytest.mark.asyncio
async def test_create_and_get_state():

    manager = StateManager()
    state = create_state()

    await manager.create_state(state)

    retrieved = await manager.get_state(state.review_id)

    assert retrieved.review_id == state.review_id
    assert retrieved.repository == "example/project"


@pytest.mark.asyncio
async def test_update_agent_result():

    manager = StateManager()
    state = create_state()

    await manager.create_state(state)

    result = create_agent_result()

    updated = await manager.update_agent_result(state.review_id, result)

    assert "SecurityAgent" in updated.agent_results
    assert updated.status == ReviewStatus.RUNNING


@pytest.mark.asyncio
async def test_update_final_review():

    manager = StateManager()
    state = create_state()

    await manager.create_state(state)

    finding = create_finding()

    updated = await manager.update_final_review(
        review_id=state.review_id,
        findings=[finding],
        recommendation=MergeRecommendation.REQUEST_CHANGES,
        summary="Critical security issue detected.",
    )

    assert updated.total_findings == 1

    assert updated.recommendation == (MergeRecommendation.REQUEST_CHANGES)

    assert updated.status == ReviewStatus.COMPLETED
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_missing_review():

    manager = StateManager()

    with pytest.raises(KeyError):
        await manager.get_state("unknown-review-id")


@pytest.mark.asyncio
async def test_duplicate_review_creation():

    manager = StateManager()
    state = create_state()

    await manager.create_state(state)

    with pytest.raises(ValueError):
        await manager.create_state(state)


@pytest.mark.asyncio
async def test_state_copy_isolation():

    manager = StateManager()
    state = create_state()

    await manager.create_state(state)

    retrieved = await manager.get_state(state.review_id)

    retrieved.changed_files.append("malicious.py")

    actual = await manager.get_state(state.review_id)

    assert "malicious.py" not in actual.changed_files
