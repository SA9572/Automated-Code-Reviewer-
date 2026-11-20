import pytest

from app.orchestration.reviewer_manager import ReviewerManager
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.schemas.review import MergeRecommendation, ReviewStatus
from app.state.change_state import ChangeState
from app.state.state_manager import StateManager


def make_finding(
    severity=Severity.MEDIUM,
    confidence=0.8,
    evidence="user input is not validated",
    title="Potential input validation issue",
    agent_name="DiffAgent",
    description="A potential issue was detected.",
    recommendation="Validate the input before processing.",
    file_path="app/main.py",
    start_line=None,
    end_line=None,
    rule_id=None,
):
    return Finding(
        agent_name=agent_name,
        title=title,
        description=description,
        severity=severity,
        confidence=confidence,
        file_path=file_path,
        evidence=evidence,
        recommendation=recommendation,
        start_line=start_line,
        end_line=end_line,
        rule_id=rule_id,
    )


def make_result(
    agent_name="DiffAgent",
    status=AgentStatus.SUCCESS,
    findings=None,
):
    return AgentResult(
        agent_name=agent_name,
        status=status,
        summary="Agent analysis completed.",
        findings=findings or [],
        execution_time_ms=100,
    )


async def create_review_state(state_manager, results):
    state = ChangeState(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
    )
    await state_manager.create_state(state)
    for result in results:
        await state_manager.update_agent_result(state.review_id, result)
    return state.review_id


@pytest.fixture
def state_manager():
    return StateManager()


@pytest.fixture
def reviewer_manager(state_manager):
    return ReviewerManager(state_manager)


def test_constructor_requires_state_manager():
    with pytest.raises(ValueError):
        ReviewerManager(None)


def test_deduplicate_findings_keeps_higher_severity():
    low = make_finding(severity=Severity.LOW, confidence=0.7)
    high = make_finding(
        severity=Severity.HIGH,
        confidence=0.9,
        agent_name="SecurityAgent",
    )

    result = ReviewerManager.deduplicate_findings([low, high])

    assert len(result) == 1
    assert result[0].severity == Severity.HIGH
    assert result[0].confidence == 0.9


def test_deduplicate_findings_normalizes_paths_and_evidence():
    first = make_finding(evidence="  User input   is not validated ")
    second = make_finding(
        evidence="user input is not validated",
        title="Different title",
        agent_name="SecurityAgent",
    )
    second.file_path = r"app\main.py"

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 1


def test_deduplication_preserves_distinct_descriptions_and_recommendations():
    first = make_finding(
        severity=Severity.MEDIUM,
        description="Input reaches a sensitive operation.",
        recommendation="Validate the input.",
    )
    second = make_finding(
        severity=Severity.HIGH,
        description="The input is attacker-controlled.",
        recommendation="Use an allowlist before processing.",
        agent_name="SecurityAgent",
    )

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 1
    assert result[0].severity == Severity.HIGH
    assert "Input reaches a sensitive operation." in result[0].description
    assert "The input is attacker-controlled." in result[0].description
    assert "Validate the input." in result[0].recommendation
    assert "Use an allowlist" in result[0].recommendation


def test_deduplication_preserves_both_evidence_details():
    first = make_finding(evidence="user-controlled value reaches query")
    second = make_finding(
        evidence="query is built without parameterization",
        agent_name="SecurityAgent",
    )

    # Matching rule and overlapping location establish the same issue.
    first.rule_id = "SQL-001"
    second.rule_id = "SQL-001"
    first.start_line, first.end_line = 10, 12
    second.start_line, second.end_line = 11, 13

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 1
    assert "user-controlled value reaches query" in result[0].evidence
    assert "query is built without parameterization" in result[0].evidence
    assert result[0].start_line == 10
    assert result[0].end_line == 13


def test_same_title_with_disjoint_line_ranges_remains_separate():
    first = make_finding(
        evidence="first occurrence",
        start_line=10,
        end_line=12,
    )
    second = make_finding(
        evidence="second occurrence",
        start_line=40,
        end_line=42,
    )

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 2


def test_conflicting_rule_ids_remain_separate():
    first = make_finding(rule_id="SEC-001")
    second = make_finding(rule_id="SEC-002")

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 2


def test_same_title_with_different_nonempty_evidence_is_not_merged():
    first = make_finding(evidence="first distinct code expression")
    second = make_finding(evidence="second distinct code expression")

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 2


def test_rank_findings_orders_by_severity_then_confidence():
    findings = [
        make_finding(Severity.LOW, 0.99),
        make_finding(Severity.HIGH, 0.70),
        make_finding(Severity.HIGH, 0.95),
        make_finding(Severity.CRITICAL, 0.60),
    ]

    result = ReviewerManager.rank_findings(findings)

    assert [item.severity for item in result] == [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.HIGH,
        Severity.LOW,
    ]
    assert result[1].confidence == 0.95


def test_high_finding_requests_changes():
    finding = make_finding(Severity.HIGH)
    result = ReviewerManager._get_recommendation([finding])
    assert result == MergeRecommendation.REQUEST_CHANGES


def test_high_finding_takes_priority_over_agent_failure():
    finding = make_finding(Severity.HIGH)
    result = ReviewerManager._get_recommendation([finding], has_failures=True)
    assert result == MergeRecommendation.REQUEST_CHANGES


def test_medium_finding_requires_review():
    finding = make_finding(Severity.MEDIUM)
    result = ReviewerManager._get_recommendation([finding])
    assert result == MergeRecommendation.REVIEW_REQUIRED


def test_low_findings_can_be_approved():
    finding = make_finding(Severity.LOW)
    result = ReviewerManager._get_recommendation([finding])
    assert result == MergeRecommendation.APPROVE


def test_failed_agent_prevents_automatic_approval():
    result = ReviewerManager._get_recommendation([], has_failures=True)
    assert result == MergeRecommendation.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_finalize_completed_review(state_manager, reviewer_manager):
    finding = make_finding(Severity.HIGH)
    review_id = await create_review_state(
        state_manager,
        [
            make_result("DiffAgent", findings=[finding]),
            make_result("StyleAgent"),
        ],
    )

    review = await reviewer_manager.finalize(review_id)

    assert review.status == ReviewStatus.COMPLETED
    assert review.recommendation == MergeRecommendation.REQUEST_CHANGES
    assert len(review.findings) == 1
    assert review.completed_at is not None


@pytest.mark.asyncio
async def test_finalize_partial_review(state_manager, reviewer_manager):
    review_id = await create_review_state(
        state_manager,
        [
            make_result("DiffAgent", findings=[make_finding(Severity.LOW)]),
            make_result("SecurityAgent", status=AgentStatus.FAILED),
        ],
    )

    review = await reviewer_manager.finalize(review_id)

    assert review.status == ReviewStatus.PARTIAL
    assert review.recommendation == MergeRecommendation.REVIEW_REQUIRED
    assert len(review.findings) == 1


@pytest.mark.asyncio
async def test_finalize_failed_review(state_manager, reviewer_manager):
    review_id = await create_review_state(
        state_manager,
        [
            make_result("DiffAgent", status=AgentStatus.FAILED),
            make_result("SecurityAgent", status=AgentStatus.TIMED_OUT),
        ],
    )

    review = await reviewer_manager.finalize(review_id)

    assert review.status == ReviewStatus.FAILED
    assert review.recommendation == MergeRecommendation.REVIEW_REQUIRED
    assert review.findings == []


@pytest.mark.asyncio
async def test_finalize_rejects_review_without_agent_results(
    state_manager,
    reviewer_manager,
):
    state = ChangeState(
        repository="test/repository",
        base_commit="abc123",
        head_commit="def456",
    )
    await state_manager.create_state(state)

    with pytest.raises(ValueError, match="without agent results"):
        await reviewer_manager.finalize(state.review_id)


def test_deduplicate_findings_merges_exact_evidence_across_rule_ids():
    first = make_finding(
        severity=Severity.CRITICAL,
        evidence="return eval(user_id)",
        title="Unsafe use of eval",
        agent_name="DiffAgent",
    )
    first.rule_id = "DIFF-001"

    second = make_finding(
        severity=Severity.CRITICAL,
        evidence=" return   eval(user_id) ",
        title="Unsafe evaluation of user input",
        agent_name="SecurityAgent",
    )
    second.rule_id = "SEC002"
    second.start_line = 4
    second.end_line = 4
    second.description = (
        "SecurityAgent independently detected unsafe evaluation "
        "of user-controlled input."
    )

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 1
    assert result[0].severity == Severity.CRITICAL
    assert result[0].start_line == 4
    assert result[0].end_line == 4
    assert "Additional context:" in result[0].description


def test_deduplicate_findings_keeps_different_evidence_with_conflicting_rules():
    first = make_finding(
        evidence="return eval(user_id)",
        title="Unsafe eval",
    )
    first.rule_id = "DIFF-001"

    second = make_finding(
        evidence="password = os.getenv('PASSWORD')",
        title="Credential configuration issue",
        agent_name="SecurityAgent",
    )
    second.rule_id = "SEC003"

    result = ReviewerManager.deduplicate_findings([first, second])

    assert len(result) == 2
