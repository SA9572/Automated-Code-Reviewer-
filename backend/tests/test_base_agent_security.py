import logging
from types import SimpleNamespace

import pytest

from app.agents.base_agent import AgentOutput, BaseAgent
from app.schemas.agent_result import AgentStatus

SECRET = "super-secret-api-key-12345"


class FailingAgent(BaseAgent):
    def __init__(self, settings):
        super().__init__(
            agent_name="SecurityTestAgent",
            llm_service=object(),
            settings=settings,
        )
        self.attempts = 0

    async def _analyze(self, context):
        self.attempts += 1
        raise RuntimeError(f"API_KEY={SECRET}")


def make_settings(retries=0):
    return SimpleNamespace(
        LLM_TIMEOUT_SECONDS=5,
        MAX_RETRIES=retries,
    )


@pytest.mark.asyncio
async def test_exception_message_is_redacted_before_returning():
    agent = FailingAgent(make_settings())

    result = await agent.execute({})

    assert result.status == AgentStatus.FAILED
    assert "RuntimeError" in result.error
    assert "[REDACTED]" in result.error
    assert SECRET not in result.error


@pytest.mark.asyncio
async def test_exception_message_is_not_returned():
    agent = FailingAgent(make_settings())

    result = await agent.execute({})

    assert result.status == AgentStatus.FAILED
    assert "RuntimeError" in result.error
    assert "[REDACTED]" in result.error
    assert SECRET not in result.error


@pytest.mark.asyncio
async def test_retry_behavior_is_preserved():
    agent = FailingAgent(make_settings(retries=2))

    result = await agent.execute({})

    assert agent.attempts == 3
    assert result.status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_retry_logs_do_not_expose_secrets(caplog):
    agent = FailingAgent(make_settings(retries=2))

    with caplog.at_level(
        logging.WARNING,
        logger="app.agents.base_agent",
    ):
        await agent.execute({})

    assert SECRET not in caplog.text
    assert "[REDACTED]" in caplog.text
    assert caplog.text.count("failed on attempt") == 2


class UnicodeTestAgent(BaseAgent):
    async def _analyze(self, context):
        return AgentOutput(summary="Unicode test completed.")


class FakeUnicodeLLMClient:
    def __init__(self, response_text):
        self.response_text = response_text

    async def get_response(self, messages, options):
        return SimpleNamespace(text=self.response_text)


@pytest.mark.asyncio
async def test_generate_response_preserves_unicode_exactly():
    """BaseAgent must preserve Unicode returned by the LLM."""

    unicode_text = (
        "Hardcoded credentials — user-controlled input; café; naïve; नमस्ते; 中文; résumé"
    )

    settings = make_settings()

    fake_client = FakeUnicodeLLMClient(unicode_text)

    fake_llm_service = SimpleNamespace(
        settings=settings,
        get_client=lambda: fake_client,
    )

    agent = UnicodeTestAgent(
        agent_name="UnicodeTestAgent",
        llm_service=fake_llm_service,
        settings=settings,
    )

    response = await agent.generate_response(
        system_prompt="You are a code review assistant.",
        user_prompt="Analyze this example.",
    )

    assert response == unicode_text
    assert "Hardcoded credentials" in response
    assert "नमस्ते" in response
    assert "中文" in response
    assert "â" not in response
