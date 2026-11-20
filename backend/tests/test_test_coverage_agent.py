import json
from unittest.mock import AsyncMock

import pytest

from app.agents.test_coverage_agent import (
    TEST_COVERAGE_SYSTEM_PROMPT,
    TestCoverageAgent,
)
from app.schemas.agent_result import AgentStatus


@pytest.fixture
def coverage_agent():
    agent = TestCoverageAgent()
    agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "Testing gaps detected.",
                "findings": [
                    {
                        "title": "Missing edge case tests",
                        "description": (
                            "The new calculation does not have tests "
                            "for empty or negative input."
                        ),
                        "severity": "MEDIUM",
                        "confidence": 0.95,
                        "file_path": "app/calculator.py",
                        "start_line": None,
                        "end_line": None,
                        "evidence": "A new calculate_total() function was added.",
                        "recommendation": (
                            "Add tests for empty input, negative values, "
                            "and boundary values."
                        ),
                        "rule_id": "TST-002",
                    }
                ],
            }
        )
    )
    return agent


@pytest.mark.asyncio
async def test_empty_diff_does_not_call_llm(coverage_agent):
    result = await coverage_agent.execute({"diff_content": ""})

    assert result.status == AgentStatus.SUCCESS
    assert result.finding_count == 0
    coverage_agent.generate_response.assert_not_called()


@pytest.mark.asyncio
async def test_successful_coverage_analysis(coverage_agent):
    result = await coverage_agent.execute(
        {
            "diff_content": "+def calculate_total(values): return sum(values)",
            "test_diff": (
                "+def test_calculate_total():\n"
                "+    assert calculate_total([1, 2, 3]) == 6"
            ),
            "changed_files": ["app/calculator.py"],
            "repository": "test-repository",
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.agent_name == "TestCoverageAgent"
    assert result.finding_count == 1

    finding = result.findings[0]
    assert finding.agent_name == "TestCoverageAgent"
    assert finding.rule_id == "TST-002"
    assert finding.severity.value == "MEDIUM"


@pytest.mark.asyncio
async def test_invalid_diff_type(coverage_agent):
    result = await coverage_agent.execute({"diff_content": 123})

    assert result.status == AgentStatus.FAILED
    assert "diff_content must be a string" in result.error


@pytest.mark.asyncio
async def test_invalid_test_diff_type(coverage_agent):
    result = await coverage_agent.execute({"diff_content": "+x = 1", "test_diff": 123})

    assert result.status == AgentStatus.FAILED
    assert "test_diff must be a string" in result.error
    coverage_agent.generate_response.assert_not_called()


@pytest.mark.parametrize("test_diff", [None, "", "   "])
def test_should_skip_when_test_diff_is_missing_or_blank(coverage_agent, test_diff):
    assert coverage_agent.should_skip({"test_diff": test_diff}) is True


def test_invalid_test_diff_type_is_not_silently_skipped(coverage_agent):
    assert coverage_agent.should_skip({"test_diff": 123}) is False


@pytest.mark.asyncio
async def test_missing_test_context_does_not_claim_repository_has_no_tests(
    coverage_agent,
):
    output = await coverage_agent._analyze(
        {"diff_content": "+def changed(): pass", "test_diff": ""}
    )

    assert output.findings == []
    assert "does not establish" in output.summary


def test_prompt_prohibits_unsafe_payload_execution():
    prompt = TEST_COVERAGE_SYSTEM_PROMPT.casefold()

    assert "never recommend executing malicious payloads" in prompt
    assert "recommend removing the unsafe mechanism" in prompt
    assert "harmless inputs and mocks" in prompt


@pytest.mark.asyncio
async def test_unknown_rule_fails(coverage_agent):
    coverage_agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "Testing gap",
                "findings": [
                    {
                        "title": "Unknown testing issue",
                        "description": "Test",
                        "severity": "LOW",
                        "confidence": 0.9,
                        "file_path": "app/test.py",
                        "evidence": "Test evidence",
                        "recommendation": "Add tests",
                        "rule_id": "UNKNOWN001",
                    }
                ],
            }
        )
    )

    result = await coverage_agent.execute(
        {
            "diff_content": "+x = 1",
            "test_diff": "+def test_example():\n+    assert True",
        }
    )

    assert result.status == AgentStatus.FAILED
    assert "Unknown test coverage rule_id" in result.error


def test_parse_valid_json():
    response = json.dumps({"summary": "No testing gaps found.", "findings": []})
    parsed = TestCoverageAgent._parse_response(response)

    assert parsed["summary"] == "No testing gaps found."
    assert parsed["findings"] == []


def test_parse_markdown_wrapped_json():
    response = """```json
    {
        "summary": "Testing review completed.",
        "findings": []
    }
    ```"""

    parsed = TestCoverageAgent._parse_response(response)
    assert parsed["summary"] == "Testing review completed."


def test_invalid_json_raises_error():
    with pytest.raises(ValueError, match="invalid JSON"):
        TestCoverageAgent._parse_response("invalid json")


def test_findings_must_be_list():
    response = json.dumps({"summary": "Test", "findings": "invalid"})

    with pytest.raises(ValueError, match="findings must be a list"):
        TestCoverageAgent._parse_response(response)


def test_non_string_response():
    with pytest.raises(ValueError, match="must be a string"):
        TestCoverageAgent._parse_response(123)


@pytest.mark.asyncio
async def test_coverage_agent_validates_locations_against_source_and_test_diffs(
    coverage_agent,
):
    coverage_agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "Testing issue found.",
                "findings": [
                    {
                        "title": "Weak test assertion",
                        "description": "The assertion does not verify the result.",
                        "severity": "MEDIUM",
                        "confidence": 0.9,
                        "file_path": "tests/test_example.py",
                        "start_line": 2,
                        "end_line": 2,
                        "evidence": "assert result is not None",
                        "recommendation": "Assert the expected value.",
                        "rule_id": "TST-005",
                    }
                ],
            }
        )
    )

    source_diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old_value = 1
+new_value = 1
"""

    test_diff = """\
diff --git a/tests/test_example.py b/tests/test_example.py
--- a/tests/test_example.py
+++ b/tests/test_example.py
@@ -1 +1,2 @@
 def test_example():
+    assert result is not None
"""

    result = await coverage_agent.execute(
        {
            "diff_content": source_diff,
            "test_diff": test_diff,
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.findings[0].start_line == 2
    assert result.findings[0].end_line == 2
