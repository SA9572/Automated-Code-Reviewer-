import json
from types import SimpleNamespace

import pytest

from app.orchestration.agent_framework_workflow import (
    AgentFrameworkWorkflow,
    ReviewWorkflowOutput,
)
from app.orchestration.parallel_orchestrator import ParallelOrchestrator
from app.schemas.agent_result import AgentResult, AgentStatus


class FakeAgent:
    def __init__(self, agent_name):
        self.agent_name = agent_name
        self.calls = 0

    async def execute(self, context):
        self.calls += 1
        return AgentResult(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary=f"{self.agent_name} completed",
            findings=[],
            execution_time_ms=10,
        )


class FakeStateManager:
    def __init__(self):
        self.saved = []

    async def update_agent_result(self, review_id, result):
        self.saved.append((review_id, result))


def build_workflow(agent_names):
    agents = [FakeAgent(name) for name in agent_names]
    state_manager = FakeStateManager()
    orchestrator = ParallelOrchestrator(
        agents=agents,
        state_manager=state_manager,
    )
    workflow = AgentFrameworkWorkflow(orchestrator)
    return workflow, agents, state_manager


@pytest.mark.asyncio
async def test_single_specialist_workflow_executes_and_persists():
    workflow, agents, state_manager = build_workflow(["DiffAgent"])

    results = await workflow.run(
        {"diff_content": "sample diff"},
        "review-single",
    )

    assert set(results) == {"DiffAgent"}
    assert agents[0].calls == 1
    assert len(state_manager.saved) == 1
    assert state_manager.saved[0][0] == "review-single"


@pytest.mark.asyncio
async def test_multiple_specialists_execute_and_persist():
    names = [
        "DiffAgent",
        "SecurityAgent",
        "StyleAgent",
        "TestCoverageAgent",
    ]
    workflow, agents, state_manager = build_workflow(names)

    results = await workflow.run(
        {"diff_content": "sample diff"},
        "review-multiple",
    )

    assert set(results) == set(names)
    assert all(agent.calls == 1 for agent in agents)
    assert len(state_manager.saved) == len(names)
    assert {
        review_id for review_id, _ in state_manager.saved
    } == {"review-multiple"}


@pytest.mark.asyncio
async def test_workflow_rejects_non_dictionary_context():
    workflow, _, _ = build_workflow(["DiffAgent"])

    with pytest.raises(ValueError, match="context"):
        await workflow.run("not a dictionary", "review-invalid")


@pytest.mark.asyncio
async def test_workflow_rejects_empty_review_id():
    workflow, _, _ = build_workflow(["DiffAgent"])

    with pytest.raises(ValueError, match="review_id"):
        await workflow.run({}, "  ")


def test_workflow_rejects_missing_or_duplicate_specialist_outputs():
    valid = AgentResult(
        agent_name="DiffAgent",
        status=AgentStatus.SUCCESS,
        summary="Completed",
    )

    with pytest.raises(RuntimeError, match="duplicate"):
        AgentFrameworkWorkflow._validate_output(
            ReviewWorkflowOutput(
                review_id="review-1",
                results=[valid, valid],
            ),
            "review-1",
            {"DiffAgent"},
        )

    with pytest.raises(RuntimeError, match="do not match"):
        AgentFrameworkWorkflow._validate_output(
            ReviewWorkflowOutput(
                review_id="review-1",
                results=[valid],
            ),
            "review-1",
            {"DiffAgent", "SecurityAgent"},
        )


def test_workflow_rejects_unexpected_specialist_output():
    result = AgentResult(
        agent_name="UnexpectedAgent",
        status=AgentStatus.SUCCESS,
        summary="Completed",
    )

    with pytest.raises(RuntimeError, match="do not match"):
        AgentFrameworkWorkflow._validate_output(
            ReviewWorkflowOutput(
                review_id="review-1",
                results=[result],
            ),
            "review-1",
            {"DiffAgent"},
        )


def test_workflow_rejects_wrong_review_id():
    result = AgentResult(
        agent_name="DiffAgent",
        status=AgentStatus.SUCCESS,
        summary="Completed",
    )

    with pytest.raises(RuntimeError, match="review ID mismatch"):
        AgentFrameworkWorkflow._validate_output(
            ReviewWorkflowOutput(
                review_id="wrong-review",
                results=[result],
            ),
            "review-1",
            {"DiffAgent"},
        )


@pytest.mark.asyncio
async def test_workflow_requires_state_manager_for_persistence():
    agent = FakeAgent("DiffAgent")
    orchestrator = ParallelOrchestrator(agents=[agent])
    workflow = AgentFrameworkWorkflow(orchestrator)

    with pytest.raises(RuntimeError, match="StateManager"):
        await workflow.run({}, "review-no-state-manager")

class FakeWorkflowRunResult:
    def __init__(self, output):
        self.output = output

    def get_outputs(self):
        return [self.output]


class FakeSDKStream:
    def __init__(self, events, output=None, failure=None):
        self.events = events
        self.output = output
        self.failure = failure
        self.closed = False
        self.close_calls = 0

    def __aiter__(self):
        async def iterate():
            for event in self.events:
                yield event
            if self.failure is not None:
                raise self.failure

        return iterate()

    async def get_final_response(self):
        return FakeWorkflowRunResult(self.output)

    def close(self):
        self.close_calls += 1
        self.closed = True


class FakeStreamingWorkflow:
    def __init__(self, stream):
        self.stream = stream

    def run(self, payload, stream=False):
        assert stream is True
        return self.stream


def _workflow_output(review_id, agent_names):
    return ReviewWorkflowOutput(
        review_id=review_id,
        results=[
            AgentResult(
                agent_name=name,
                status=AgentStatus.SUCCESS,
                summary=f"{name} completed",
                findings=[],
                execution_time_ms=1,
            )
            for name in agent_names
        ],
    )


@pytest.mark.asyncio
async def test_stream_completion_serializes_events_and_persists_results():
    workflow, agents, state_manager = build_workflow(["DiffAgent", "SecurityAgent"])
    output = _workflow_output("review-stream", ["DiffAgent", "SecurityAgent"])
    sdk_event = SimpleNamespace(
        executor_id="specialist-DiffAgent",
        event_id="event-1",
        timestamp="2026-11-17T10:00:00Z",
        payload={"api_key": "must-not-be-exposed"},
    )
    stream = FakeSDKStream([sdk_event], output)
    workflow.workflow = FakeStreamingWorkflow(stream)

    events = [
        event
        async for event in workflow.run_stream(
            {"diff_content": "safe"},
            "review-stream",
        )
    ]

    assert events == [
        {
            "event_type": "SimpleNamespace",
            "executor_id": "specialist-DiffAgent",
            "event_id": "event-1",
            "timestamp": "2026-11-17T10:00:00Z",
        }
    ]
    assert "must-not-be-exposed" not in str(events)
    assert json.loads(json.dumps(events)) == events
    assert stream.closed is True
    assert stream.close_calls == 1
    assert len(state_manager.saved) == 2
    assert {review_id for review_id, _ in state_manager.saved} == {
        "review-stream"
    }


@pytest.mark.asyncio
async def test_early_stream_closure_closes_sdk_stream_without_persisting():
    workflow, _, state_manager = build_workflow(["DiffAgent"])
    stream = FakeSDKStream(
        [SimpleNamespace(executor_id="specialist-DiffAgent")],
        _workflow_output("review-early-close", ["DiffAgent"]),
    )
    workflow.workflow = FakeStreamingWorkflow(stream)

    event_iterator = workflow.run_stream({}, "review-early-close")
    first_event = await anext(event_iterator)
    assert first_event["event_type"] == "SimpleNamespace"

    await event_iterator.aclose()

    assert stream.closed is True
    assert stream.close_calls == 1
    assert state_manager.saved == []


@pytest.mark.asyncio
async def test_stream_cancellation_closes_sdk_stream_without_persisting():
    import asyncio

    workflow, _, state_manager = build_workflow(["DiffAgent"])
    entered_wait = asyncio.Event()
    never_release = asyncio.Event()

    class BlockingStream(FakeSDKStream):
        def __aiter__(self):
            async def iterate():
                yield SimpleNamespace(executor_id="specialist-DiffAgent")
                entered_wait.set()
                await never_release.wait()

            return iterate()

    stream = BlockingStream(
        [],
        _workflow_output("review-cancel", ["DiffAgent"]),
    )
    workflow.workflow = FakeStreamingWorkflow(stream)

    async def consume():
        async for _ in workflow.run_stream({}, "review-cancel"):
            pass

    task = asyncio.create_task(consume())
    await entered_wait.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert stream.closed is True
    assert stream.close_calls == 1
    assert state_manager.saved == []


@pytest.mark.asyncio
async def test_stream_failure_closes_sdk_stream_without_persisting():
    workflow, _, state_manager = build_workflow(["DiffAgent"])
    stream = FakeSDKStream(
        [SimpleNamespace(executor_id="specialist-DiffAgent")],
        _workflow_output("review-failure", ["DiffAgent"]),
        failure=RuntimeError("simulated SDK stream failure"),
    )
    workflow.workflow = FakeStreamingWorkflow(stream)

    with pytest.raises(RuntimeError, match="simulated SDK stream failure"):
        _ = [
            event
            async for event in workflow.run_stream({}, "review-failure")
        ]

    assert stream.closed is True
    assert stream.close_calls == 1
    assert state_manager.saved == []


@pytest.mark.asyncio
async def test_stream_persistence_failure_still_closes_sdk_stream():
    class FailingStateManager(FakeStateManager):
        async def update_agent_result(self, review_id, result):
            raise RuntimeError("simulated persistence failure")

    agent = FakeAgent("DiffAgent")
    state_manager = FailingStateManager()
    orchestrator = ParallelOrchestrator(
        agents=[agent],
        state_manager=state_manager,
    )
    workflow = AgentFrameworkWorkflow(orchestrator)
    stream = FakeSDKStream(
        [SimpleNamespace(executor_id="specialist-DiffAgent")],
        _workflow_output("review-persist-failure", ["DiffAgent"]),
    )
    workflow.workflow = FakeStreamingWorkflow(stream)

    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        _ = [
            event
            async for event in workflow.run_stream({}, "review-persist-failure")
        ]

    assert stream.closed is True
    assert stream.close_calls == 1


