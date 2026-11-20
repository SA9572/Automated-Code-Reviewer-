"""Phase 3 resource-ownership and cleanup tests for ReviewPipeline."""

import pytest

from app.orchestration.review_pipeline import ReviewPipeline


class TrackingService:
    def __init__(self, *, fail_times=0):
        self.close_calls = 0
        self.fail_times = fail_times

    async def close(self):
        self.close_calls += 1
        if self.close_calls <= self.fail_times:
            raise RuntimeError("simulated service close failure")


class InjectedAgent:
    def __init__(self, name, service):
        self.agent_name = name
        self.llm_service = service

    async def execute(self, context):
        raise AssertionError("execute() should not be called by close()")


class InjectedFixAgent:
    def __init__(self, service):
        self.llm_service = service


@pytest.mark.asyncio
async def test_pipeline_closes_owned_agent_and_fix_services():
    """The default pipeline owns its default agents and default fix agent."""
    pipeline = ReviewPipeline()
    agent_services = [TrackingService() for _ in pipeline.orchestrator.agents]
    for agent, service in zip(pipeline.orchestrator.agents, agent_services):
        agent.llm_service = service
    fix_service = TrackingService()
    pipeline.fix_suggestion_agent.llm_service = fix_service

    await pipeline.close()

    assert all(service.close_calls == 1 for service in agent_services)
    assert fix_service.close_calls == 1


@pytest.mark.asyncio
async def test_pipeline_does_not_close_injected_agent_or_fix_services():
    """Services belonging to injected components remain caller-owned."""
    agent_service = TrackingService()
    fix_service = TrackingService()
    agents = [InjectedAgent("DiffAgent", agent_service)]
    fix_agent = InjectedFixAgent(fix_service)
    pipeline = ReviewPipeline(agents=agents, fix_suggestion_agent=fix_agent)

    await pipeline.close()

    assert agent_service.close_calls == 0
    assert fix_service.close_calls == 0


@pytest.mark.asyncio
async def test_pipeline_closes_shared_owned_service_only_once():
    """Identity-deduplication prevents double-closing a shared service."""
    pipeline = ReviewPipeline()
    shared_service = TrackingService()
    for agent in pipeline.orchestrator.agents:
        agent.llm_service = shared_service
    pipeline.fix_suggestion_agent.llm_service = shared_service

    await pipeline.close()

    assert shared_service.close_calls == 1


@pytest.mark.asyncio
async def test_pipeline_close_is_idempotent_after_success():
    pipeline = ReviewPipeline()
    service = TrackingService()
    for agent in pipeline.orchestrator.agents:
        agent.llm_service = service
    pipeline.fix_suggestion_agent.llm_service = service

    await pipeline.close()
    await pipeline.close()

    assert service.close_calls == 1


@pytest.mark.asyncio
async def test_pipeline_close_attempts_remaining_services_after_failure():
    pipeline = ReviewPipeline()
    failing_service = TrackingService(fail_times=1)
    successful_service = TrackingService()
    for index, agent in enumerate(pipeline.orchestrator.agents):
        agent.llm_service = failing_service if index == 0 else successful_service
    pipeline.fix_suggestion_agent.llm_service = successful_service

    with pytest.raises(RuntimeError):
        await pipeline.close()

    assert failing_service.close_calls == 1
    assert successful_service.close_calls == 1


@pytest.mark.asyncio
async def test_pipeline_can_retry_service_that_failed_to_close():
    """Failed services remain retryable; already-closed services stay closed."""
    pipeline = ReviewPipeline()
    failing_service = TrackingService(fail_times=1)
    successful_service = TrackingService()
    for index, agent in enumerate(pipeline.orchestrator.agents):
        agent.llm_service = failing_service if index == 0 else successful_service
    pipeline.fix_suggestion_agent.llm_service = successful_service

    with pytest.raises(RuntimeError):
        await pipeline.close()

    await pipeline.close()

    assert failing_service.close_calls == 2
    assert successful_service.close_calls == 1

