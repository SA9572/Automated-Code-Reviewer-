import json
from unittest.mock import AsyncMock
import pytest
from app.agents.fix_suggestion_agent import FixSuggestionAgent
from app.schemas.finding import Finding, Severity
from app.schemas.fix_suggestion import (
    FixApprovalStatus,
    FixSuggestion,
    PatchValidationStatus,
)
@pytest.fixture
def agent():
    return FixSuggestionAgent()
@pytest.fixture
def finding():
    return Finding(
        finding_id="finding-001",
        agent_name="SecurityAgent",
        title="Potential SQL injection",
        description="User input is interpolated into a SQL query.",
        severity=Severity.HIGH,
        confidence=0.98,
        file_path="app/users.py",
        start_line=10,
        end_line=10,
        evidence='query = f"SELECT * FROM users WHERE name = {username}"',
        recommendation="Use a parameterized SQL query.",
        rule_id="SEC001",
    )
@pytest.fixture
def valid_diff():
    return (
        "--- a/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@ -10,1 +10,1 @@\n"
        '-query = f"SELECT * FROM users WHERE name = {username}"\n'
        '+query = "SELECT * FROM users WHERE name = ?"\n'
    )
@pytest.fixture
def valid_candidate(valid_diff):
    return {
        "finding_id": "finding-001",
        "file_path": "app/users.py",
        "title": "Use a parameterized query",
        "explanation": "Replace direct interpolation with a parameter placeholder.",
        "unified_diff": valid_diff,
    }
def test_constructor(agent):
    assert agent.agent_name == "FixSuggestionAgent"
@pytest.mark.asyncio
async def test_empty_findings_returns_no_suggestions(agent):
    agent.generate_response = AsyncMock()
    result = await agent.generate_suggestions(
        review_id="review-001",
        findings=[],
        diff_content="some diff",
    )
    assert result == []
    agent.generate_response.assert_not_called()
@pytest.mark.asyncio
async def test_empty_diff_returns_no_suggestions(agent, finding):
    agent.generate_response = AsyncMock()
    result = await agent.generate_suggestions(
        review_id="review-001",
        findings=[finding],
        diff_content="   ",
    )
    assert result == []
    agent.generate_response.assert_not_called()
@pytest.mark.parametrize("review_id", ["", "   ", None, 123])
@pytest.mark.asyncio
async def test_invalid_review_id_rejected(agent, finding, review_id):
    with pytest.raises(ValueError):
        await agent.generate_suggestions(
            review_id=review_id,
            findings=[finding],
            diff_content="diff",
        )
@pytest.mark.parametrize("findings", ["invalid", None, {}, [123]])
@pytest.mark.asyncio
async def test_invalid_findings_rejected(agent, findings):
    with pytest.raises(ValueError):
        await agent.generate_suggestions(
            review_id="review-001",
            findings=findings,
            diff_content="diff",
        )
@pytest.mark.parametrize("diff_content", [None, 123, {}, []])
@pytest.mark.asyncio
async def test_invalid_diff_type_rejected(agent, finding, diff_content):
    with pytest.raises(ValueError, match="diff_content must be a string"):
        await agent.generate_suggestions(
            review_id="review-001",
            findings=[finding],
            diff_content=diff_content,
        )
def test_parse_valid_json(agent, valid_candidate):
    response = json.dumps({"fix_suggestions": [valid_candidate]})
    result = agent._parse_response(response)
    assert len(result) == 1
    assert result[0]["finding_id"] == "finding-001"
def test_parse_fenced_json(agent, valid_candidate):
    response = (
        "```json\n"
        + json.dumps({"fix_suggestions": [valid_candidate]})
        + "\n```"
    )
    result = agent._parse_response(response)
    assert len(result) == 1
@pytest.mark.parametrize("response", ["", "   ", None, 123])
def test_empty_or_non_string_response_rejected(agent, response):
    with pytest.raises(ValueError):
        agent._parse_response(response)
def test_invalid_json_rejected(agent):
    with pytest.raises(ValueError, match="invalid JSON"):
        agent._parse_response("{invalid json}")
@pytest.mark.parametrize(
    "response",
    [
        "[]",
        '"text"',
        '{"other": []}',
        '{"fix_suggestions": {}}',
        '{"fix_suggestions": [123]}',
    ],
)
def test_invalid_response_structure_rejected(agent, response):
    with pytest.raises(ValueError):
        agent._parse_response(response)
def test_build_valid_suggestion(agent, finding, valid_candidate):
    suggestions = agent._build_suggestions(
        review_id="review-001",
        findings=[finding],
        candidates=[valid_candidate],
    )
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert isinstance(suggestion, FixSuggestion)
    assert suggestion.review_id == "review-001"
    assert suggestion.finding_id == finding.finding_id
    assert suggestion.file_path == finding.file_path
    assert suggestion.validation_status == PatchValidationStatus.UNVALIDATED
    assert suggestion.approval_status == FixApprovalStatus.PENDING
def test_unknown_finding_id_is_skipped(agent, finding, valid_candidate):
    valid_candidate["finding_id"] = "unknown-finding"
    suggestions = agent._build_suggestions(
        review_id="review-001",
        findings=[finding],
        candidates=[valid_candidate],
    )
    assert suggestions == []
def test_mismatched_file_path_is_skipped(agent, finding, valid_candidate):
    valid_candidate["file_path"] = "app/other.py"
    suggestions = agent._build_suggestions(
        review_id="review-001",
        findings=[finding],
        candidates=[valid_candidate],
    )
    assert suggestions == []
@pytest.mark.parametrize(
    "field",
    ["title", "explanation", "unified_diff"],
)
def test_missing_candidate_field_is_skipped(
    agent, finding, valid_candidate, field
):
    valid_candidate[field] = ""
    suggestions = agent._build_suggestions(
        review_id="review-001",
        findings=[finding],
        candidates=[valid_candidate],
    )
    assert suggestions == []
def test_invalid_unified_diff_is_skipped(agent, finding, valid_candidate):
    valid_candidate["unified_diff"] = "this is not a unified diff"
    suggestions = agent._build_suggestions(
        review_id="review-001",
        findings=[finding],
        candidates=[valid_candidate],
    )
    assert suggestions == []
def test_multiple_valid_suggestions_are_retained(
    agent, finding, valid_candidate
):
    second_finding = finding.model_copy(
        update={
            "finding_id": "finding-002",
            "file_path": "app/database.py",
            "title": "Unsafe database query",
        },
        deep=True,
    )
    second_candidate = {
        "finding_id": "finding-002",
        "file_path": "app/database.py",
        "title": "Use parameterized SQL",
        "explanation": "Avoid direct SQL interpolation.",
        "unified_diff": (
            "--- a/app/database.py\n"
            "+++ b/app/database.py\n"
            "@@ -5,1 +5,1 @@\n"
            '-query = f"SELECT * FROM users WHERE id = {user_id}"\n'
            '+query = "SELECT * FROM users WHERE id = ?"\n'
        ),
    }
    suggestions = agent._build_suggestions(
        review_id="review-001",
        findings=[finding, second_finding],
        candidates=[valid_candidate, second_candidate],
    )
    assert len(suggestions) == 2
    assert {item.finding_id for item in suggestions} == {
        "finding-001",
        "finding-002",
    }
def test_single_incomplete_hunk_header_can_be_repaired(agent):
    incomplete_diff = (
        "--- a/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@\n"
        "-old_value = 1\n"
        "+new_value = 2\n"
    )
    repaired = agent._repair_single_hunk_header(
        unified_diff=incomplete_diff,
        start_line=10,
    )
    assert "@@ -10,1 +10,1 @@" in repaired
    assert agent._has_basic_unified_diff_structure(
        repaired,
        "app/users.py",
    )
def test_multiple_incomplete_hunks_are_not_guessed(agent):
    incomplete_diff = (
        "--- a/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@\n"
        "-old_value = 1\n"
        "+new_value = 2\n"
        "@@\n"
        "-old_value = 3\n"
        "+new_value = 4\n"
    )
    repaired = agent._repair_single_hunk_header(
        unified_diff=incomplete_diff,
        start_line=10,
    )
    assert repaired == incomplete_diff
def test_diff_with_wrong_path_is_rejected(agent, valid_diff):
    assert not agent._has_basic_unified_diff_structure(
        valid_diff,
        "app/other.py",
    )
def test_diff_with_wrong_hunk_counts_is_rejected(agent):
    malformed_diff = (
        "--- a/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@ -10,2 +10,1 @@\n"
        '-query = f"SELECT * FROM users WHERE name = {username}"\n'
        '+query = "SELECT * FROM users WHERE name = ?"\n'
    )
    assert not agent._has_basic_unified_diff_structure(
        malformed_diff,
        "app/users.py",
    )
def test_structural_validation_does_not_claim_semantic_correctness(
    agent, valid_diff
):
    # This patch has valid unified-diff structure.
    # It does not demonstrate that the SQL execution binds username.
    assert agent._has_basic_unified_diff_structure(
        valid_diff,
        "app/users.py",
    )
def test_prompt_requires_complete_unified_diff(agent, finding):
    prompt = agent._build_user_prompt(
        [finding],
        "+changed_line = True",
    )
    assert "complete unified diff hunk" in prompt
    assert "accurate old and new line counts" in prompt
    assert finding.finding_id in prompt
def test_fix_suggestion_schema_prevents_unvalidated_approval(valid_diff):
    with pytest.raises(ValueError, match="valid patch"):
        FixSuggestion(
            review_id="review-001",
            finding_id="finding-001",
            file_path="app/users.py",
            title="Fix SQL query",
            explanation="Use a validated patch.",
            unified_diff=valid_diff,
            approval_status=FixApprovalStatus.APPROVED,
        )
def test_fix_suggestion_schema_accepts_pending_suggestion(valid_diff):
    suggestion = FixSuggestion(
        review_id="review-001",
        finding_id="finding-001",
        file_path="app/users.py",
        title="Fix SQL query",
        explanation="Proposed parameterized query.",
        unified_diff=valid_diff,
    )
    assert suggestion.approval_status == FixApprovalStatus.PENDING
    assert suggestion.validation_status == PatchValidationStatus.UNVALIDATED


@pytest.mark.asyncio
async def test_truncated_json_response_retries_once_and_recovers(
    agent, finding, valid_candidate
):
    valid_response = json.dumps({"fix_suggestions": [valid_candidate]})
    agent.generate_response = AsyncMock(
        side_effect=['{"fix_suggestions":[', valid_response]
    )

    suggestions = await agent.generate_suggestions(
        review_id="review-retry",
        findings=[finding],
        diff_content="original diff",
    )

    assert len(suggestions) == 1
    assert agent.generate_response.await_count == 2


@pytest.mark.asyncio
async def test_non_truncated_invalid_json_does_not_retry(agent, finding):
    agent.generate_response = AsyncMock(return_value="{invalid json}")

    with pytest.raises(ValueError, match="invalid JSON"):
        await agent.generate_suggestions(
            review_id="review-no-retry",
            findings=[finding],
            diff_content="original diff",
        )

    agent.generate_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_truncated_json_retry_is_bounded_to_one_attempt(
    agent, finding
):
    agent.generate_response = AsyncMock(
        side_effect=['{"fix_suggestions":[', '{"fix_suggestions":[' ]
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        await agent.generate_suggestions(
            review_id="review-bounded",
            findings=[finding],
            diff_content="original diff",
        )

    assert agent.generate_response.await_count == 2


def test_unified_diff_with_git_metadata_preamble_is_accepted(agent):
    diff = (
        "diff --git a/app/users.py b/app/users.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-old_value = 1\n"
        "+new_value = 2\n"
    )
    assert agent._has_basic_unified_diff_structure(diff, "app/users.py")


def test_unified_diff_with_mismatched_git_metadata_path_is_rejected(agent):
    diff = (
        "diff --git a/app/other.py b/app/other.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-old_value = 1\n"
        "+new_value = 2\n"
    )
    assert not agent._has_basic_unified_diff_structure(diff, "app/users.py")
@pytest.mark.asyncio
async def test_fenced_truncated_json_response_retries_once(
    agent, finding, valid_candidate
):
    truncated = '```json\n{"fix_suggestions": ['
    valid_response = (
        "```json\n"
        + json.dumps({"fix_suggestions": [valid_candidate]})
        + "\n```"
    )

    agent.generate_response = AsyncMock(
        side_effect=[truncated, valid_response]
    )

    suggestions = await agent.generate_suggestions(
        review_id="review-fenced-retry",
        findings=[finding],
        diff_content="original diff",
    )

    assert len(suggestions) == 1
    assert agent.generate_response.await_count == 2