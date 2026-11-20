import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_framework import ChatOptions, Message

from app.config import Settings, get_settings
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding
from app.security.secret_redactor import redact_text
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


def contains_mojibake(text: str) -> bool:
    """
    Detect common UTF-8 decoding artifacts.

    This is diagnostic only. It does not modify or repair text.
    """

    markers = (
        "â€",
        "â€™",
        "â€œ",
        "Ã",
        "â€“",
        "â€”",
        "�",
    )

    if any(marker in text for marker in markers):
        return True

    # Detect suspicious sequences such as:
    # edgeâcase, errorâhandling, unitâtest.
    return bool(re.search(r"â(?=[a-z])", text))


@dataclass
class AgentOutput:
    """Internal output returned by a specialist agent."""

    summary: str = ""
    findings: list[Finding] = field(default_factory=list)


class BaseAgent(ABC):
    """
    Common foundation for all code-review agents.

    Handles LLM communication, timeout, retries,
    execution timing and standardized results.
    """

    def __init__(
        self,
        agent_name: str,
        llm_service: LLMService | None = None,
        settings: Settings | None = None,
    ):
        if not agent_name.strip():
            raise ValueError("agent_name cannot be empty")

        self.agent_name = agent_name
        self.settings = (
            settings or getattr(llm_service, "settings", None) or get_settings()
        )
        self.llm_service = llm_service or LLMService(self.settings)

    def get_llm_client(self):
        """Return the shared OpenAI-compatible LLM client."""

        return self.llm_service.get_client()

    async def generate_response(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Generate a non-empty response using bounded LLM retries."""

        client = self.get_llm_client()
        max_retries = max(0, int(self.settings.MAX_RETRIES))

        logger.debug(
            "%s prompt diagnostics: system_mojibake=%s, user_mojibake=%s",
            self.agent_name,
            contains_mojibake(system_prompt),
            contains_mojibake(user_prompt),
        )

        messages = [
            Message(role="system", contents=[system_prompt]),
            Message(role="user", contents=[user_prompt]),
        ]

        for attempt in range(max_retries + 1):
            try:
                response = await client.get_response(
                    messages=messages,
                    options=ChatOptions(temperature=0.1),
                )

                response_text = getattr(response, "text", None)

                if response_text and response_text.strip():
                    logger.debug(
                        "%s response diagnostics: response_type=%s, "
                        "response_mojibake=%s, response_length=%d",
                        self.agent_name,
                        type(response).__name__,
                        contains_mojibake(response_text),
                        len(response_text),
                    )

                    return response_text.strip()

                logger.warning(
                    "%s received an empty LLM response on attempt %d/%d. "
                    "model=%s, finish_reason=%s, message_count=%d",
                    self.agent_name,
                    attempt + 1,
                    max_retries + 1,
                    getattr(response, "model", None),
                    getattr(response, "finish_reason", None),
                    len(getattr(response, "messages", []) or []),
                )

                if attempt < max_retries:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                if attempt >= max_retries:
                    raise

                logger.warning(
                    "%s LLM request failed on attempt %d/%d: %s",
                    self.agent_name,
                    attempt + 1,
                    max_retries + 1,
                    type(exc).__name__,
                )

                await asyncio.sleep(min(0.5 * (2**attempt), 2.0))

        raise ValueError(
            f"{self.agent_name}: LLM returned an empty response "
            f"after {max_retries + 1} attempts"
        )

    @abstractmethod
    async def _analyze(self, context: dict[str, Any]) -> AgentOutput:
        """
        Implement the specialist's analysis logic.

        Subclasses must return an AgentOutput.
        """

        raise NotImplementedError

    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute the agent and return a standardized result."""

        start_time = time.perf_counter()
        timeout = self.settings.LLM_TIMEOUT_SECONDS
        max_retries = self.settings.MAX_RETRIES

        for attempt in range(max_retries + 1):
            try:
                output = await asyncio.wait_for(
                    self._analyze(context),
                    timeout=timeout,
                )

                if not isinstance(output, AgentOutput):
                    raise TypeError("_analyze() must return an AgentOutput instance")

                result = AgentResult(
                    agent_name=self.agent_name,
                    status=AgentStatus.SUCCESS,
                    summary=output.summary,
                    findings=output.findings,
                    execution_time_ms=self._elapsed_ms(start_time),
                )

                logger.info(
                    "%s completed successfully with %d findings",
                    self.agent_name,
                    result.finding_count,
                )

                return result

            except TimeoutError:
                elapsed = self._elapsed_ms(start_time)

                logger.warning(
                    "%s timed out after %d ms",
                    self.agent_name,
                    elapsed,
                )

                return AgentResult(
                    agent_name=self.agent_name,
                    status=AgentStatus.TIMED_OUT,
                    summary="Agent execution exceeded the configured timeout.",
                    execution_time_ms=elapsed,
                    error=f"Execution timed out after {timeout} seconds.",
                )

            except Exception as exc:
                exception_type = type(exc).__name__
                safe_error = redact_text(str(exc))

                if attempt < max_retries:
                    logger.warning(
                        "%s failed on attempt %d/%d (%s): %s",
                        self.agent_name,
                        attempt + 1,
                        max_retries + 1,
                        exception_type,
                        safe_error,
                    )

                    continue

                # log() records the error without attaching an exception traceback.
                logger.log(
                    logging.ERROR,
                    "%s failed after all retry attempts (%s): %s",
                    self.agent_name,
                    exception_type,
                    safe_error,
                )

                elapsed = self._elapsed_ms(start_time)

                return AgentResult(
                    agent_name=self.agent_name,
                    status=AgentStatus.FAILED,
                    summary="Agent execution failed.",
                    execution_time_ms=elapsed,
                    error=f"{exception_type}: {safe_error}",
                )

        return AgentResult(
            agent_name=self.agent_name,
            status=AgentStatus.FAILED,
            error="Agent execution ended unexpectedly.",
            execution_time_ms=self._elapsed_ms(start_time),
        )

    @staticmethod
    def _elapsed_ms(start_time: float) -> int:
        return max(0, int((time.perf_counter() - start_time) * 1000))
