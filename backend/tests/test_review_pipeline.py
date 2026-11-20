import asyncio

import pytest

from app.agents.base_agent import AgentOutput, BaseAgent
from app.orchestration.review_pipeline import ReviewPipeline
from app.schemas.agent_result import AgentStatus
from app.schemas.finding import Finding, Severity
from app.schemas.review import MergeRecommendation, ReviewStatus
from app.state.state_manager import StateManager


class MockAgent(BaseAgent):
    def __init__(
        self,
        agent_name,
        findings=None,
        should_fail=False,
    ):
        self.agent_name = agent_name
        self.findings = findings or []
        self.should_fail = should_fail

    async def execute(self, context):
        if self.should_fail:
            from app.schemas.agent_result import AgentResult

            return AgentResult(
                agent_name=self.agent_name,
                status=AgentStatus.FAILED,
                summary="Mock agent failure",
                findings=[],
                execution_time_ms=10,
                error="Simulated failure",
            )

        from app.schemas.agent_result import AgentResult

        return AgentResult(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary="Mock analysis completed",
            findings=self.findings,
            execution_time_ms=10,
        )

    async def _analyze(self, context):
        return AgentOutput(
            summary="Mock analysis completed",
            findings=self.findings,
        )


def make_finding(severity=Severity.LOW):
    return Finding(
        agent_name="DiffAgent",
        title="Potential code issue",
        description="A potential issue was detected.",
        severity=severity,
        confidence=0.95,
        file_path="app/main.py",
        evidence="example problematic code",
        recommendation="Review and fix the code.",
    )


@pytest.mark.asyncio
async def test_pipeline_completes_successfully():
    state_manager = StateManager()

    agents = [
        MockAgent("DiffAgent", [make_finding(Severity.HIGH)]),
        MockAgent("StyleAgent"),
        MockAgent("SecurityAgent"),
        MockAgent("TestCoverageAgent"),
    ]

    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=agents,
    )

    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="--- a/app/main.py\n+++ b/app/main.py",
        changed_files=["app/main.py"],
    )

    assert review.status == ReviewStatus.COMPLETED
    assert review.recommendation == MergeRecommendation.REQUEST_CHANGES
    assert len(review.agent_results) == 4
    assert len(review.findings) == 1


@pytest.mark.asyncio
async def test_pipeline_persists_agent_results():
    state_manager = StateManager()

    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent"),
            MockAgent("StyleAgent"),
        ],
    )

    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
    )

    state = await state_manager.get_state(review.review_id)

    assert len(state.agent_results) == 2
    assert state.status == ReviewStatus.COMPLETED


@pytest.mark.asyncio
async def test_pipeline_handles_partial_agent_failure():
    state_manager = StateManager()

    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent", [make_finding(Severity.LOW)]),
            MockAgent("SecurityAgent", should_fail=True),
        ],
    )

    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
    )

    assert review.status == ReviewStatus.PARTIAL
    assert review.recommendation == MergeRecommendation.REVIEW_REQUIRED
    assert len(review.agent_results) == 2


@pytest.mark.asyncio
async def test_pipeline_all_agents_fail():
    state_manager = StateManager()

    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent", should_fail=True),
            MockAgent("SecurityAgent", should_fail=True),
        ],
    )

    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
    )

    assert review.status == ReviewStatus.FAILED
    assert review.recommendation == MergeRecommendation.REVIEW_REQUIRED
    assert review.findings == []


@pytest.mark.asyncio
async def test_pipeline_rejects_empty_repository():
    pipeline = ReviewPipeline(
        agents=[MockAgent("DiffAgent")],
    )

    with pytest.raises(ValueError, match="repository"):
        await pipeline.run(
            repository="",
            base_commit="abc123",
            head_commit="def456",
            diff_content="sample diff",
        )


@pytest.mark.asyncio
async def test_pipeline_rejects_invalid_diff_type():
    pipeline = ReviewPipeline(
        agents=[MockAgent("DiffAgent")],
    )

    with pytest.raises(ValueError, match="diff_content"):
        await pipeline.run(
            repository="test/repository",
            base_commit="abc123",
            head_commit="def456",
            diff_content=None,
        )
class MockFixSuggestionAgent:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []
    async def generate_suggestions(self, review_id, findings, diff_content):
        self.calls.append({
            "review_id": review_id,
            "findings": findings,
            "diff_content": diff_content,
        })
        if self.should_fail:
            raise RuntimeError("Simulated fix suggestion failure")
        from app.schemas.fix_suggestion import FixSuggestion
        finding = findings[0]
        return [
            FixSuggestion(
                review_id=review_id,
                finding_id=finding.finding_id,
                file_path=finding.file_path,
                title="Correct the identified issue",
                explanation="Apply a targeted correction.",
                unified_diff=(
                    "--- a/app/main.py\n"
                    "+++ b/app/main.py\n"
                    "@@ -1,1 +1,1 @@\n"
                    "-old_value = 1\n"
                    "+new_value = 1\n"
                ),
            )
        ]
@pytest.mark.asyncio
async def test_pipeline_generates_and_persists_fix_suggestions():
    state_manager = StateManager()
    finding = make_finding(Severity.HIGH)
    fix_agent = MockFixSuggestionAgent()
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[MockAgent("DiffAgent", [finding])],
        fix_suggestion_agent=fix_agent,
    )
    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="--- a/app/main.py\n+++ b/app/main.py\n",
        changed_files=["app/main.py"],
    )
    saved_suggestions = await state_manager.get_fix_suggestions(
        review.review_id
    )
    assert review.status == ReviewStatus.COMPLETED
    assert len(review.findings) == 1
    assert len(fix_agent.calls) == 1
    assert len(saved_suggestions) == 1
    assert saved_suggestions[0].review_id == review.review_id
    assert saved_suggestions[0].finding_id == review.findings[0].finding_id
@pytest.mark.asyncio
async def test_pipeline_returns_review_when_fix_suggestion_generation_fails():
    state_manager = StateManager()
    finding = make_finding(Severity.HIGH)
    fix_agent = MockFixSuggestionAgent(should_fail=True)
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[MockAgent("DiffAgent", [finding])],
        fix_suggestion_agent=fix_agent,
    )
    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
        changed_files=["app/main.py"],
    )
    saved_suggestions = await state_manager.get_fix_suggestions(
        review.review_id
    )
    assert review.status == ReviewStatus.COMPLETED
    assert len(review.findings) == 1
    assert len(fix_agent.calls) == 1
    assert saved_suggestions == []
@pytest.mark.asyncio
async def test_pipeline_skips_fix_suggestions_when_no_findings_exist():
    state_manager = StateManager()
    fix_agent = MockFixSuggestionAgent()
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[MockAgent("DiffAgent")],
        fix_suggestion_agent=fix_agent,
    )
    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
        changed_files=["app/main.py"],
    )
    assert review.status == ReviewStatus.COMPLETED
    assert review.findings == []
    assert fix_agent.calls == []
    assert await state_manager.get_fix_suggestions(review.review_id) == []
def make_agent_finding(
    agent_name,
    title,
    severity,
    evidence,
    rule_id=None,
):
    return Finding(
        agent_name=agent_name,
        title=title,
        description="Finding produced by a specialist agent.",
        severity=severity,
        confidence=0.95,
        file_path="app/main.py",
        start_line=12,
        end_line=12,
        evidence=evidence,
        recommendation="Review and correct the identified issue.",
        rule_id=rule_id,
    )
@pytest.mark.asyncio
async def test_pipeline_consolidates_duplicate_findings_across_agents():
    state_manager = StateManager()
    diff_finding = make_agent_finding(
        agent_name="DiffAgent",
        title="Unsafe query construction",
        severity=Severity.LOW,
        evidence='query = "SELECT * FROM users WHERE id = " + user_id',
        rule_id="DIFF-001",
    )
    security_finding = make_agent_finding(
        agent_name="SecurityAgent",
        title="Unsafe query construction",
        severity=Severity.HIGH,
        evidence='query = "SELECT * FROM users WHERE id = " + user_id',
        rule_id="SEC-001",
    )
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent", [diff_finding]),
            MockAgent("SecurityAgent", [security_finding]),
        ],
        fix_suggestion_agent=MockFixSuggestionAgent(),
    )
    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
        changed_files=["app/main.py"],
    )
    assert review.status == ReviewStatus.COMPLETED
    assert len(review.agent_results) == 2
    assert len(review.findings) == 1
    assert review.findings[0].severity == Severity.HIGH
    assert review.recommendation == MergeRecommendation.REQUEST_CHANGES
@pytest.mark.asyncio
async def test_pipeline_preserves_distinct_findings_and_prioritizes_high_severity():
    state_manager = StateManager()
    high_finding = make_agent_finding(
        agent_name="SecurityAgent",
        title="SQL injection risk",
        severity=Severity.HIGH,
        evidence="SQL query uses untrusted input.",
        rule_id="SEC-001",
    )
    medium_finding = make_agent_finding(
        agent_name="StyleAgent",
        title="Missing type annotation",
        severity=Severity.MEDIUM,
        evidence="Function parameter has no type annotation.",
        rule_id="STYLE-001",
    )
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("SecurityAgent", [high_finding]),
            MockAgent("StyleAgent", [medium_finding]),
        ],
        fix_suggestion_agent=MockFixSuggestionAgent(),
    )
    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
        changed_files=["app/main.py"],
    )
    assert review.status == ReviewStatus.COMPLETED
    assert len(review.agent_results) == 2
    assert len(review.findings) == 2
    assert {finding.severity for finding in review.findings} == {
        Severity.HIGH,
        Severity.MEDIUM,
    }
    assert review.recommendation == MergeRecommendation.REQUEST_CHANGES
@pytest.mark.asyncio
async def test_pipeline_approves_multiple_low_findings_from_successful_agents():
    state_manager = StateManager()
    diff_finding = make_agent_finding(
        agent_name="DiffAgent",
        title="Minor code improvement",
        severity=Severity.LOW,
        evidence="A minor implementation improvement is possible.",
        rule_id="DIFF-LOW-001",
    )
    style_finding = make_agent_finding(
        agent_name="StyleAgent",
        title="Minor naming improvement",
        severity=Severity.LOW,
        evidence="A local variable could use a clearer name.",
        rule_id="STYLE-LOW-001",
    )
    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent", [diff_finding]),
            MockAgent("StyleAgent", [style_finding]),
        ],
        fix_suggestion_agent=MockFixSuggestionAgent(),
    )
    review = await pipeline.run(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
        diff_content="sample diff",
        changed_files=["app/main.py"],
    )
    assert review.status == ReviewStatus.COMPLETED
    assert len(review.agent_results) == 2
    assert len(review.findings) == 2
    assert all(
        finding.severity == Severity.LOW
        for finding in review.findings
    )
    assert review.recommendation == MergeRecommendation.APPROVE



@pytest.mark.asyncio
async def test_pipeline_cancellation_persists_failed_status():
    state_manager = StateManager()
    agent_started = asyncio.Event()

    class BlockingAgent(BaseAgent):
        def __init__(self):
            self.agent_name = "BlockingAgent"

        async def execute(self, context):
            agent_started.set()
            await asyncio.Event().wait()

        async def _analyze(self, context):
            return AgentOutput(summary="Not expected to complete")

    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[BlockingAgent()],
    )

    review_task = asyncio.create_task(
        pipeline.run(
            repository="test/repository",
            base_commit="abc123",
            head_commit="def456",
            diff_content="--- a/app/main.py\n+++ b/app/main.py",
            changed_files=["app/main.py"],
        )
    )

    await asyncio.wait_for(agent_started.wait(), timeout=5)

    review_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await review_task

    states, total = await state_manager.list_states(limit=10)

    assert total == 1
    assert states[0].status == ReviewStatus.FAILED



