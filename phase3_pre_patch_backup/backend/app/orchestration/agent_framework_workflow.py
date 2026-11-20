
"""Microsoft Agent Framework graph for specialist code-review execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from collections.abc import AsyncIterator
from typing import Any, Never

from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.parallel_orchestrator import ParallelOrchestrator
from app.schemas.agent_result import AgentResult

logger = logging.getLogger(__name__)


class ReviewWorkflowInput(BaseModel):
    """Typed input passed through the specialist-agent workflow graph."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    context: dict[str, Any]
    review_id: str = Field(min_length=1)
    sensitive_values: list[str] = Field(default_factory=list)


class AgentExecutionEnvelope(BaseModel):
    """Typed message emitted by one specialist executor."""

    review_id: str
    result: AgentResult


class ReviewWorkflowOutput(BaseModel):
    """Typed output emitted by the fan-in executor."""

    review_id: str
    results: list[AgentResult]


class _Dispatcher(Executor):
    """Dispatch the typed review input to specialist executors."""

    @handler
    async def dispatch(
        self,
        payload: ReviewWorkflowInput,
        ctx: WorkflowContext[ReviewWorkflowInput],
    ) -> None:
        await ctx.send_message(payload)


class _SpecialistExecutor(Executor):
    """Execute one specialist agent through the existing orchestrator."""

    def __init__(
        self,
        agent: Any,
        orchestrator: ParallelOrchestrator,
    ):
        super().__init__(id=f"specialist-{agent.agent_name}")
        self.agent = agent
        self.orchestrator = orchestrator

    @handler
    async def process(
        self,
        payload: ReviewWorkflowInput,
        ctx: WorkflowContext[AgentExecutionEnvelope],
    ) -> None:
        result = await self.orchestrator._execute_agent(
            self.agent,
            payload.context,
            set(payload.sensitive_values),
        )

        await ctx.send_message(
            AgentExecutionEnvelope(
                review_id=payload.review_id,
                result=result,
            )
        )


class _ReviewFanInExecutor(Executor):
    """Aggregate specialist results into one typed workflow output."""

    def __init__(self):
        super().__init__(id="review-results-aggregator")

    async def _yield_results(
        self,
        messages: list[AgentExecutionEnvelope],
        ctx: WorkflowContext[Never, ReviewWorkflowOutput],
    ) -> None:
        if not messages:
            raise ValueError(
                "The review workflow returned no specialist results."
            )

        review_ids = {message.review_id for message in messages}

        if len(review_ids) != 1:
            raise ValueError(
                "Specialist results contain inconsistent review IDs."
            )

        results = sorted(
            (message.result for message in messages),
            key=lambda result: result.agent_name,
        )

        await ctx.yield_output(
            ReviewWorkflowOutput(
                review_id=review_ids.pop(),
                results=results,
            )
        )

    @handler
    async def aggregate_many(
        self,
        messages: list[AgentExecutionEnvelope],
        ctx: WorkflowContext[Never, ReviewWorkflowOutput],
    ) -> None:
        """Handle the list emitted by a multi-agent fan-in edge."""

        await self._yield_results(messages, ctx)

    @handler
    async def aggregate_one(
        self,
        message: AgentExecutionEnvelope,
        ctx: WorkflowContext[Never, ReviewWorkflowOutput],
    ) -> None:
        """Handle the individual message from a single-agent edge."""

        await self._yield_results([message], ctx)


class AgentFrameworkWorkflow:
    """Build and run the Microsoft Agent Framework fan-out/fan-in graph."""

    def __init__(self, orchestrator: ParallelOrchestrator):
        if orchestrator is None:
            raise ValueError("orchestrator is required")

        if not orchestrator.agents:
            raise ValueError(
                "At least one specialist agent is required."
            )

        self.orchestrator = orchestrator

        self.dispatcher = _Dispatcher(id="review-dispatcher")

        self.specialists = [
            _SpecialistExecutor(agent, orchestrator)
            for agent in orchestrator.agents
        ]

        self.aggregator = _ReviewFanInExecutor()

        builder = WorkflowBuilder(start_executor=self.dispatcher)

        if len(self.specialists) == 1:
            # A fan-out group requires at least two targets.
            builder.add_edge(
                self.dispatcher,
                self.specialists[0],
            )

            builder.add_edge(
                self.specialists[0],
                self.aggregator,
            )

        else:
            # Execute two or more specialists in parallel.
            builder.add_fan_out_edges(
                self.dispatcher,
                self.specialists,
            )

            builder.add_fan_in_edges(
                self.specialists,
                self.aggregator,
            )

        self.workflow = builder.build()

    @staticmethod
    def _validate_output(
        output_data: Any,
        review_id: str,
        expected_names: set[str],
    ) -> dict[str, AgentResult]:
        """Validate workflow output and reject missing or duplicate agents."""

        output = ReviewWorkflowOutput.model_validate(output_data)

        if output.review_id != review_id:
            raise RuntimeError(
                "Workflow output review ID mismatch."
            )

        returned_names = [
            result.agent_name
            for result in output.results
        ]

        if len(returned_names) != len(set(returned_names)):
            raise RuntimeError(
                "Workflow returned duplicate specialist results."
            )

        if set(returned_names) != expected_names:
            raise RuntimeError(
                "Workflow results do not match configured specialists. "
                f"Expected {sorted(expected_names)}, "
                f"received {sorted(set(returned_names))}."
            )

        return {
            result.agent_name: result
            for result in output.results
        }

    async def _persist_results(
        self,
        review_id: str,
        results: dict[str, AgentResult],
    ) -> None:
        """Persist validated and sanitized specialist results."""

        state_manager = self.orchestrator.state_manager

        if state_manager is None:
            raise RuntimeError(
                "StateManager is required for review execution."
            )

        for result in results.values():
            await state_manager.update_agent_result(
                review_id,
                result,
            )

    async def run(
        self,
        context: dict[str, Any],
        review_id: str,
    ) -> dict[str, AgentResult]:
        """Run the workflow and persist sanitized specialist results."""

        if not isinstance(context, dict):
            raise ValueError("context must be a dictionary")

        if not isinstance(review_id, str) or not review_id.strip():
            raise ValueError(
                "review_id must be a non-empty string"
            )

        # Reuse the existing security redaction source of truth.
        from app.security.secret_redactor import extract_sensitive_values

        sensitive_values = extract_sensitive_values(
            context.get("diff_content"),
            context.get("test_diff"),
        )

        payload = ReviewWorkflowInput(
            context=context,
            review_id=review_id,
            sensitive_values=sorted(sensitive_values or set()),
        )

        run_result = await self.workflow.run(payload)
        outputs = run_result.get_outputs()

        if len(outputs) != 1:
            raise RuntimeError(
                "Expected exactly one output from the review workflow; "
                f"received {len(outputs)}."
            )

        expected_names = {
            agent.agent_name
            for agent in self.orchestrator.agents
        }

        results = self._validate_output(
            outputs[0],
            review_id,
            expected_names,
        )

        await self._persist_results(review_id, results)

        return results

    @staticmethod
    def serialize_event(event: Any) -> dict[str, Any]:
        """Project an SDK event onto a deliberately small, JSON-safe schema.

        Workflow event payloads may contain source code, prompts, credentials,
        or arbitrary SDK objects. They are intentionally not forwarded.
        """
        event_type = type(event).__name__
        serialized: dict[str, Any] = {"event_type": event_type}

        executor_id = getattr(event, "executor_id", None)
        if isinstance(executor_id, str):
            serialized["executor_id"] = executor_id

        event_id = getattr(event, "event_id", None)
        if isinstance(event_id, (str, int)):
            serialized["event_id"] = event_id

        timestamp = getattr(event, "timestamp", None)
        if isinstance(timestamp, (datetime, date)):
            serialized["timestamp"] = timestamp.isoformat()
        elif isinstance(timestamp, str):
            serialized["timestamp"] = timestamp

        return serialized

    async def run_stream(
        self,
        context: dict[str, Any],
        review_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream safe event projections; persist only after normal completion."""

        if not isinstance(context, dict):
            raise ValueError("context must be a dictionary")

        if not isinstance(review_id, str) or not review_id.strip():
            raise ValueError("review_id must be a non-empty string")

        from app.security.secret_redactor import extract_sensitive_values

        sensitive_values = extract_sensitive_values(
            context.get("diff_content"),
            context.get("test_diff"),
        )

        payload = ReviewWorkflowInput(
            context=context,
            review_id=review_id,
            sensitive_values=sorted(sensitive_values or set()),
        )

        # Use the actual Microsoft Agent Framework streaming API.
        stream = self.workflow.run(payload, stream=True)

        try:
            async for event in stream:
                yield self.serialize_event(event)

            # This point is reached only after normal stream exhaustion.
            run_result = await stream.get_final_response()
            outputs = run_result.get_outputs()

            if len(outputs) != 1:
                raise RuntimeError(
                    "Expected exactly one output from the review workflow; "
                    f"received {len(outputs)}."
                )

            expected_names = {
                agent.agent_name
                for agent in self.orchestrator.agents
            }

            results = self._validate_output(
                outputs[0],
                review_id,
                expected_names,
            )

            await self._persist_results(review_id, results)

        finally:
            # Close the SDK stream on completion, exceptions, cancellation,
            # or early consumer termination. Shield cleanup so cancellation
            # does not interrupt the SDK's asynchronous close operation.
            close_task = None
            try:
                close_task = asyncio.ensure_future(stream.close())
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                # Wait for the shielded cleanup task before propagating the
                # original cancellation to the caller.
                if close_task is not None:
                    try:
                        await close_task
                    except Exception:
                        logger.exception(
                            "Failed to close Microsoft Agent Framework stream "
                            "during cancellation."
                        )
                raise
            except Exception:
                logger.exception("Failed to close Microsoft Agent Framework stream.")
