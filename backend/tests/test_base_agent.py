import asyncio

import pytest

from app.agents.base_agent import AgentOutput, BaseAgent
from app.config import Settings
from app.schemas.agent_result import AgentStatus


class FakeLLMService:
    def __init__(self, settings):
        self.settings = settings

    def get_client(self):
        return "fake-client"


class SuccessfulAgent(BaseAgent):
    async def _analyze(self, context):
        return AgentOutput(
            summary="Code analysis completed.",
            findings=[],
        )


class FailingAgent(BaseAgent):
    async def _analyze(self, context):
        raise RuntimeError("Simulated failure")


class TimeoutAgent(BaseAgent):
    async def _analyze(self, context):
        await asyncio.sleep(0.2)
        return AgentOutput(summary="Finished")


class InvalidOutputAgent(BaseAgent):
    async def _analyze(self, context):
        return "Invalid output"


@pytest.fixture
def settings():
    return Settings(
        GROQ_API_KEY="test-api-key",
        GROQ_MODEL="test-model",
        LLM_TIMEOUT_SECONDS=1,
        MAX_RETRIES=0,
    )


def make_agent(agent_class, settings):
    return agent_class(
        agent_name="TestAgent",
        llm_service=FakeLLMService(settings),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_successful_execution(settings):
    agent = make_agent(SuccessfulAgent, settings)

    result = await agent.execute({})

    assert result.status == AgentStatus.SUCCESS
    assert result.agent_name == "TestAgent"
    assert result.summary == "Code analysis completed."
    assert result.execution_time_ms >= 0


@pytest.mark.asyncio
async def test_failed_execution(settings):
    agent = make_agent(FailingAgent, settings)

    result = await agent.execute({})

    assert result.status == AgentStatus.FAILED
    assert "Simulated failure" in result.error


@pytest.mark.asyncio
async def test_timeout_execution(settings):
    timeout_settings = Settings(
        GROQ_API_KEY="test-api-key",
        GROQ_MODEL="test-model",
        LLM_TIMEOUT_SECONDS=0.05,
        MAX_RETRIES=0,
    )

    agent = make_agent(TimeoutAgent, timeout_settings)

    result = await agent.execute({})

    assert result.status == AgentStatus.TIMED_OUT
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_invalid_agent_output(settings):
    agent = make_agent(InvalidOutputAgent, settings)

    result = await agent.execute({})

    assert result.status == AgentStatus.FAILED
    assert "AgentOutput" in result.error


@pytest.mark.asyncio
async def test_agent_retries_after_failure(settings):
    class RetryAgent(BaseAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.attempts = 0

        async def _analyze(self, context):
            self.attempts += 1

            if self.attempts == 1:
                raise RuntimeError("Temporary error")

            return AgentOutput(summary="Succeeded after retry")

    retry_settings = Settings(
        GROQ_API_KEY="test-api-key",
        GROQ_MODEL="test-model",
        LLM_TIMEOUT_SECONDS=1,
        MAX_RETRIES=1,
    )

    agent = RetryAgent(
        agent_name="RetryAgent",
        llm_service=FakeLLMService(retry_settings),
        settings=retry_settings,
    )

    result = await agent.execute({})

    assert result.status == AgentStatus.SUCCESS
    assert agent.attempts == 2
