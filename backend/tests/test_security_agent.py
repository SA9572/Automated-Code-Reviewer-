import json
from unittest.mock import AsyncMock

import pytest

from app.agents.security_agent import SecurityAgent
from app.schemas.agent_result import AgentStatus


@pytest.fixture
def security_agent():

    agent = SecurityAgent()

    agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "SQL injection vulnerability detected.",
                "findings": [
                    {
                        "title": "Potential SQL injection",
                        "description": (
                            "User-controlled input is directly "
                            "concatenated into an SQL query."
                        ),
                        "severity": "CRITICAL",
                        "confidence": 0.98,
                        "file_path": "app/database.py",
                        "start_line": 10,
                        "end_line": 10,
                        "evidence": (
                            'query = "SELECT * FROM users WHERE id = " + user_id'
                        ),
                        "recommendation": ("Use parameterized SQL queries."),
                        "rule_id": "SEC001",
                    }
                ],
            }
        )
    )

    return agent


@pytest.mark.asyncio
async def test_empty_diff_does_not_call_llm(security_agent):

    result = await security_agent.execute({"diff_content": ""})

    assert result.status == AgentStatus.SUCCESS
    assert result.finding_count == 0

    security_agent.generate_response.assert_not_called()


@pytest.mark.asyncio
async def test_successful_security_analysis(security_agent):

    result = await security_agent.execute(
        {
            "diff_content": '+query = "SELECT * FROM users WHERE id = " + user_id',
            "changed_files": ["app/database.py"],
            "repository": "test-repository",
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.agent_name == "SecurityAgent"
    assert result.finding_count == 1

    finding = result.findings[0]

    assert finding.rule_id == "SEC001"
    assert finding.severity.value == "CRITICAL"


@pytest.mark.asyncio
async def test_invalid_diff_type(security_agent):

    result = await security_agent.execute({"diff_content": 123})

    assert result.status == AgentStatus.FAILED
    assert "diff_content must be a string" in result.error


@pytest.mark.asyncio
async def test_unknown_security_rule_fails(security_agent):

    security_agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "Security issue",
                "findings": [
                    {
                        "title": "Unknown vulnerability",
                        "description": "Test",
                        "severity": "HIGH",
                        "confidence": 0.9,
                        "file_path": "app/test.py",
                        "evidence": "test",
                        "recommendation": "Fix it",
                        "rule_id": "UNKNOWN001",
                    }
                ],
            }
        )
    )

    result = await security_agent.execute({"diff_content": "+dangerous_code()"})

    assert result.status == AgentStatus.FAILED
    assert "Unknown security rule_id" in result.error


def test_parse_valid_json():

    response = json.dumps({"summary": "No vulnerabilities found.", "findings": []})

    parsed = SecurityAgent._parse_response(response)

    assert parsed["summary"] == "No vulnerabilities found."
    assert parsed["findings"] == []


def test_parse_markdown_wrapped_json():

    response = """```json
    {
        "summary": "Security review completed.",
        "findings": []
    }
    ```"""

    parsed = SecurityAgent._parse_response(response)

    assert parsed["summary"] == "Security review completed."


def test_invalid_json_raises_error():

    with pytest.raises(ValueError, match="invalid JSON"):
        SecurityAgent._parse_response("invalid json")


def test_findings_must_be_list():

    response = json.dumps({"summary": "Test", "findings": "invalid"})

    with pytest.raises(ValueError, match="findings must be a list"):
        SecurityAgent._parse_response(response)


@pytest.mark.asyncio
async def test_security_agent_keeps_only_verified_line_numbers(security_agent):
    diff = """\
diff --git a/app/database.py b/app/database.py
--- a/app/database.py
+++ b/app/database.py
@@ -10 +10 @@
-old_query = "SELECT 1"
+query = "SELECT * FROM users WHERE id = " + user_id
"""

    result = await security_agent.execute(
        {
            "diff_content": diff,
            "changed_files": ["app/database.py"],
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.findings[0].start_line == 10
    assert result.findings[0].end_line == 10
