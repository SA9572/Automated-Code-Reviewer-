import json
from unittest.mock import AsyncMock

import pytest

from app.agents.style_agent import StyleAgent
from app.schemas.agent_result import AgentStatus


@pytest.fixture
def style_agent():

    agent = StyleAgent()

    agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "Style issues detected.",
                "findings": [
                    {
                        "title": "Incorrect variable naming",
                        "description": (
                            "Variable naming does not follow "
                            "Python snake_case conventions."
                        ),
                        "severity": "LOW",
                        "confidence": 0.95,
                        "file_path": "app/example.py",
                        "start_line": 5,
                        "end_line": 5,
                        "evidence": "UserName = 'John'",
                        "recommendation": ("Rename UserName to user_name."),
                        "rule_id": "STYLE-001",
                    }
                ],
            }
        )
    )

    return agent


@pytest.mark.asyncio
async def test_empty_diff_does_not_call_llm(style_agent):

    result = await style_agent.execute({"diff_content": ""})

    assert result.status == AgentStatus.SUCCESS
    assert result.finding_count == 0

    style_agent.generate_response.assert_not_called()


@pytest.mark.asyncio
async def test_successful_style_analysis(style_agent):

    result = await style_agent.execute(
        {
            "diff_content": "+UserName = 'John'",
            "changed_files": ["app/example.py"],
            "repository": "test-repository",
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.agent_name == "StyleAgent"
    assert result.finding_count == 1

    finding = result.findings[0]

    assert finding.agent_name == "StyleAgent"
    assert finding.rule_id == "STYLE-001"
    assert finding.severity.value == "LOW"


@pytest.mark.asyncio
async def test_invalid_diff_type(style_agent):

    result = await style_agent.execute({"diff_content": 123})

    assert result.status == AgentStatus.FAILED
    assert "diff_content must be a string" in result.error


def test_parse_valid_json():

    response = json.dumps({"summary": "No issues found.", "findings": []})

    parsed = StyleAgent._parse_response(response)

    assert parsed["summary"] == "No issues found."
    assert parsed["findings"] == []


def test_parse_markdown_wrapped_json():

    response = """```json
    {
        "summary": "Style review completed.",
        "findings": []
    }
    ```"""

    parsed = StyleAgent._parse_response(response)

    assert parsed["summary"] == "Style review completed."


def test_invalid_json_raises_error():

    with pytest.raises(ValueError, match="invalid JSON"):
        StyleAgent._parse_response("invalid json")


def test_findings_must_be_list():

    response = json.dumps({"summary": "Test", "findings": "invalid"})

    with pytest.raises(ValueError, match="findings must be a list"):
        StyleAgent._parse_response(response)


def test_non_object_finding_fails(style_agent):

    style_agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {"summary": "Invalid response", "findings": ["not a finding object"]}
        )
    )

    import asyncio

    result = asyncio.run(style_agent.execute({"diff_content": "+x = 1"}))

    assert result.status == AgentStatus.FAILED
    assert "Each finding must be a JSON object" in result.error


@pytest.mark.asyncio
async def test_style_agent_clears_unverified_line_numbers(style_agent):
    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old_name = "John"
+UserName = "John"
"""

    result = await style_agent.execute(
        {
            "diff_content": diff,
            "changed_files": ["app/example.py"],
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.findings[0].start_line is None
    assert result.findings[0].end_line is None
