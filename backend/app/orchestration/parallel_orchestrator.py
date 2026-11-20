import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from app.agents.base_agent import BaseAgent
from app.agents.diff_agent import DiffAgent
from app.agents.security_agent import SecurityAgent
from app.agents.style_agent import StyleAgent
from app.agents.test_coverage_agent import TestCoverageAgent
from app.schemas.agent_result import AgentResult, AgentStatus
from app.security.secret_redactor import (
    extract_sensitive_values,
    redact_agent_result,
)
from app.state.state_manager import StateManager

logger = logging.getLogger(__name__)


class ParallelOrchestrator:
    """
    Executes specialist code-review agents concurrently.

    Responsibilities:
    - Run specialist agents in parallel.
    - Limit concurrent executions.
    - Isolate individual agent failures.
    - Skip agents when required context is unavailable.
    - Sanitize sensitive information before persistence.
    - Return standardized AgentResult objects.
    - Optionally persist results through StateManager.
    """

    def __init__(
        self,
        agents: Sequence[BaseAgent] | None = None,
        state_manager: StateManager | None = None,
        max_concurrency: int = 4,
    ):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")

        self.agents = list(
            agents
            if agents is not None
            else [
                DiffAgent(),
                StyleAgent(),
                SecurityAgent(),
                TestCoverageAgent(),
            ]
        )

        if not self.agents:
            raise ValueError("At least one agent must be provided")

        agent_names = [agent.agent_name for agent in self.agents]

        if len(agent_names) != len(set(agent_names)):
            raise ValueError("Agent names must be unique")

        self.state_manager = state_manager
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run(
        self,
        context: dict[str, Any],
        review_id: str | None = None,
    ) -> dict[str, AgentResult]:
        """
        Execute all configured agents concurrently.

        Returns:
            Dictionary mapping agent names to AgentResult objects.
        """

        if not isinstance(context, dict):
            raise ValueError("context must be a dictionary")

        if review_id is not None and self.state_manager is None:
            raise ValueError("state_manager is required when review_id is supplied")

        # Extract sensitive values once so every agent result
        # can be sanitized using the same source of truth.
        sensitive_values = extract_sensitive_values(
            context.get("diff_content"),
            context.get("test_diff"),
        )

        logger.info(
            "Starting parallel review with %d agents",
            len(self.agents),
        )

        tasks = [
            self._execute_agent(agent, context, sensitive_values)
            for agent in self.agents
        ]

        results_list = await asyncio.gather(*tasks)

        results = {result.agent_name: result for result in results_list}

        # Persist sanitized specialist results.
        if review_id is not None:
            for result in results.values():
                await self.state_manager.update_agent_result(
                    review_id,
                    result,
                )

        successful = sum(
            result.status == AgentStatus.SUCCESS for result in results.values()
        )

        skipped = sum(
            result.status == AgentStatus.SKIPPED for result in results.values()
        )

        failed = sum(
            result.status
            in {
                AgentStatus.FAILED,
                AgentStatus.TIMED_OUT,
            }
            for result in results.values()
        )

        logger.info(
            "Parallel review finished: %d successful, %d skipped, %d failed",
            successful,
            skipped,
            failed,
        )

        return results

    async def _execute_agent(
        self,
        agent: BaseAgent,
        context: dict[str, Any],
        sensitive_values: set[str] | None = None,
    ) -> AgentResult:
        """Execute one agent with concurrency and failure isolation."""

        # Preserve compatibility with direct calls that pass only
        # agent and context.
        if sensitive_values is None:
            sensitive_values = extract_sensitive_values(
                context.get("diff_content"),
                context.get("test_diff"),
            )

        async with self._semaphore:
            try:
                logger.info(
                    "Starting agent: %s",
                    agent.agent_name,
                )

                # Check whether the agent has sufficient input context.
                should_skip = getattr(agent, "should_skip", None)

                if callable(should_skip) and should_skip(context):
                    logger.info(
                        "Skipping agent %s because required context is unavailable",
                        agent.agent_name,
                    )

                    skipped_result = AgentResult(
                        agent_name=agent.agent_name,
                        status=AgentStatus.SKIPPED,
                        summary=(
                            "Analysis skipped because required "
                            "context was not supplied."
                        ),
                    )

                    return redact_agent_result(
                        skipped_result,
                        sensitive_values,
                    )

                # Execute the specialist agent.
                result = await agent.execute(context.copy())

                if not isinstance(result, AgentResult):
                    raise TypeError(f"{agent.agent_name} must return AgentResult")

                if result.agent_name != agent.agent_name:
                    raise ValueError(
                        "AgentResult agent_name does not match "
                        f"executing agent: {agent.agent_name}"
                    )

                # Sanitize before returning or persisting the result.
                result = redact_agent_result(
                    result,
                    sensitive_values,
                )

                logger.info(
                    "Agent %s completed with status %s",
                    agent.agent_name,
                    result.status.value,
                )

                return result

            except asyncio.CancelledError:
                logger.warning(
                    "Agent %s was cancelled",
                    agent.agent_name,
                )
                raise

            except Exception as exc:
                # Do not log the exception message or traceback here.
                # An exception may contain an unredacted credential.
                logger.warning(
                    "Agent %s failed unexpectedly (%s)",
                    agent.agent_name,
                    type(exc).__name__,
                )

                failed_result = AgentResult(
                    agent_name=agent.agent_name,
                    status=AgentStatus.FAILED,
                    summary="Unexpected agent execution failure.",
                    error=f"Agent execution failed: {type(exc).__name__}",
                )

                return redact_agent_result(
                    failed_result,
                    sensitive_values,
                )
