import asyncio
from unittest.mock import AsyncMock

import pytest

from app.orchestration.parallel_orchestrator import ParallelOrchestrator
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity


class FakeAgent:
    """Lightweight agent for orchestrator unit tests."""

    def __init__(
        self,
        agent_name="FakeAgent",
        result=None,
        exception=None,
        should_skip=False,
    ):
        self.agent_name = agent_name
        self.result = result
        self.exception = exception
        self.skip = should_skip
        self.execute_calls = 0

    def should_skip(self, context):
        return self.skip

    async def execute(self, context):
        self.execute_calls += 1

        if self.exception:
            raise self.exception

        if callable(self.result):
            return await self.result(context)

        return self.result


def make_finding():
    return Finding(
        agent_name="FakeAgent",
        title='Hardcoded password="admin123" detected',
        description='Found password="admin123" in source.',
        severity=Severity.HIGH,
        confidence=0.95,
        file_path="backend/config.py",
        evidence='password="admin123"',
        recommendation='Replace password="admin123" with an environment variable.',
        rule_id="SEC001",
    )


def make_result(agent_name="FakeAgent"):
    return AgentResult(
        agent_name=agent_name,
        status=AgentStatus.SUCCESS,
        summary='Found password="admin123".',
        findings=[make_finding()],
    )


@pytest.mark.asyncio
async def test_successful_agent_execution():
    agent = FakeAgent(result=make_result())

    orchestrator = ParallelOrchestrator(agents=[agent])

    results = await orchestrator.run({"diff_content": "+x = 1"})

    assert "FakeAgent" in results
    assert results["FakeAgent"].status == AgentStatus.SUCCESS
    assert agent.execute_calls == 1


@pytest.mark.asyncio
async def test_agent_result_is_redacted_before_returning():
    agent = FakeAgent(result=make_result())

    orchestrator = ParallelOrchestrator(agents=[agent])

    results = await orchestrator.run({"diff_content": "+x = 1"})

    result = results["FakeAgent"]

    assert "admin123" not in result.summary
    assert "admin123" not in result.findings[0].title
    assert "admin123" not in result.findings[0].description
    assert "admin123" not in result.findings[0].evidence
    assert "admin123" not in result.findings[0].recommendation


@pytest.mark.asyncio
async def test_sanitized_result_is_persisted():
    agent = FakeAgent(result=make_result())
    state_manager = AsyncMock()

    orchestrator = ParallelOrchestrator(
        agents=[agent],
        state_manager=state_manager,
    )

    results = await orchestrator.run(
        {"diff_content": "+x = 1"},
        review_id="review-123",
    )

    state_manager.update_agent_result.assert_awaited_once()

    persisted_review_id, persisted_result = (
        state_manager.update_agent_result.await_args.args
    )

    assert persisted_review_id == "review-123"
    assert persisted_result == results["FakeAgent"]
    assert "admin123" not in persisted_result.summary
    assert "admin123" not in persisted_result.findings[0].evidence


@pytest.mark.asyncio
async def test_skipped_agent_is_not_executed():
    agent = FakeAgent(
        should_skip=True,
        result=make_result(),
    )

    orchestrator = ParallelOrchestrator(agents=[agent])

    results = await orchestrator.run({"diff_content": "+x = 1"})

    result = results["FakeAgent"]

    assert result.status == AgentStatus.SKIPPED
    assert agent.execute_calls == 0
    assert result.findings == []


@pytest.mark.asyncio
async def test_skipped_agent_result_is_persisted():
    agent = FakeAgent(should_skip=True)
    state_manager = AsyncMock()

    orchestrator = ParallelOrchestrator(
        agents=[agent],
        state_manager=state_manager,
    )

    results = await orchestrator.run(
        {"diff_content": "+x = 1"},
        review_id="review-456",
    )

    state_manager.update_agent_result.assert_awaited_once()

    _, persisted_result = state_manager.update_agent_result.await_args.args

    assert persisted_result.status == AgentStatus.SKIPPED
    assert results["FakeAgent"].status == AgentStatus.SKIPPED


@pytest.mark.asyncio
async def test_agent_exception_returns_safe_failure():
    secret = "password=admin123"
    agent = FakeAgent(exception=RuntimeError(secret))

    orchestrator = ParallelOrchestrator(agents=[agent])

    results = await orchestrator.run({"diff_content": "+x = 1"})

    result = results["FakeAgent"]

    assert result.status == AgentStatus.FAILED
    assert "admin123" not in result.error
    assert "admin123" not in result.summary
    assert result.findings == []


@pytest.mark.asyncio
async def test_agent_result_name_mismatch_returns_failure():
    agent = FakeAgent(
        agent_name="FakeAgent",
        result=make_result(agent_name="DifferentAgent"),
    )

    orchestrator = ParallelOrchestrator(agents=[agent])

    results = await orchestrator.run({"diff_content": "+x = 1"})

    result = results["FakeAgent"]

    assert result.status == AgentStatus.FAILED
    assert result.error is not None
    assert result.findings == []


@pytest.mark.asyncio
async def test_agents_execute_with_concurrency_limit():
    active_agents = 0
    maximum_active_agents = 0

    async def slow_execute(context):
        nonlocal active_agents, maximum_active_agents

        active_agents += 1
        maximum_active_agents = max(
            maximum_active_agents,
            active_agents,
        )

        try:
            await asyncio.sleep(0.02)
            return AgentResult(
                agent_name=context["agent_name"],
                status=AgentStatus.SUCCESS,
            )
        finally:
            active_agents -= 1

    agents = [
        FakeAgent(
            agent_name=f"Agent{i}",
            result=slow_execute,
        )
        for i in range(4)
    ]

    orchestrator = ParallelOrchestrator(
        agents=agents,
        max_concurrency=2,
    )

    # Give each agent its own identifying context through a wrapper.
    for agent in agents:
        original_execute = agent.execute
        agent_name = agent.agent_name

        async def execute_with_name(
            context,
            original=original_execute,
            name=agent_name,
        ):
            return await original({**context, "agent_name": name})

        agent.execute = execute_with_name

    results = await orchestrator.run({"diff_content": "+x = 1"})

    assert len(results) == 4
    assert all(result.status == AgentStatus.SUCCESS for result in results.values())
    assert maximum_active_agents <= 2
    assert maximum_active_agents == 2


@pytest.mark.asyncio
async def test_invalid_agent_result_returns_failure():
    agent = FakeAgent(result={"status": "SUCCESS"})

    orchestrator = ParallelOrchestrator(agents=[agent])

    results = await orchestrator.run({"diff_content": "+x = 1"})

    assert results["FakeAgent"].status == AgentStatus.FAILED


def test_orchestrator_rejects_zero_concurrency():
    agent = FakeAgent(result=make_result())

    with pytest.raises(ValueError, match="max_concurrency"):
        ParallelOrchestrator(
            agents=[agent],
            max_concurrency=0,
        )


def test_orchestrator_rejects_duplicate_agent_names():
    agents = [
        FakeAgent(agent_name="DuplicateAgent"),
        FakeAgent(agent_name="DuplicateAgent"),
    ]

    with pytest.raises(ValueError, match="unique"):
        ParallelOrchestrator(agents=agents)
