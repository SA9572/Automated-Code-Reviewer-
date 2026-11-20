import json
from unittest.mock import AsyncMock

import pytest

from app.agents.diff_agent import DiffAgent
from app.config import Settings
from app.schemas.agent_result import AgentStatus


class FakeLLMService:
    def __init__(self, settings=None):
        self.settings = settings or Settings(
            GROQ_API_KEY="test-api-key",
            GROQ_MODEL="test-model",
            LLM_TIMEOUT_SECONDS=5,
            MAX_RETRIES=0,
        )

    def get_client(self):
        return None


@pytest.mark.asyncio
async def test_empty_diff_does_not_call_llm():
    agent = DiffAgent(llm_service=FakeLLMService())

    result = await agent.execute(
        {
            "diff_content": "",
        }
    )

    assert result.status == AgentStatus.SUCCESS
    assert result.findings == []
    assert "No code changes" in result.summary


def test_parse_valid_json():
    response = """
    {
      "summary": "Potential bug found",
      "findings": []
    }
    """

    parsed = DiffAgent._parse_response(response)

    assert parsed["summary"] == "Potential bug found"
    assert parsed["findings"] == []


def test_parse_markdown_wrapped_json():
    response = """```json
    {
      "summary": "No issues",
      "findings": []
    }
    ```"""

    parsed = DiffAgent._parse_response(response)

    assert parsed["summary"] == "No issues"


def test_invalid_json_raises_error():
    with pytest.raises(ValueError, match="invalid JSON"):
        DiffAgent._parse_response("not valid JSON")


def test_findings_must_be_list():
    with pytest.raises(ValueError, match="findings must be a list"):
        DiffAgent._parse_response('{"summary": "test", "findings": {}}')


@pytest.mark.asyncio
async def test_diff_agent_clears_unverified_line_numbers():
    agent = DiffAgent()
    agent.generate_response = AsyncMock(
        return_value=json.dumps(
            {
                "summary": "Issue found",
                "findings": [
                    {
                        "title": "Potential defect",
                        "description": "A defect was identified.",
                        "severity": "MEDIUM",
                        "confidence": 0.9,
                        "file_path": "app/example.py",
                        "start_line": 99,
                        "end_line": 99,
                        "evidence": "example",
                        "recommendation": "Fix the defect.",
                        "rule_id": "DIFF-001",
                    }
                ],
            }
        )
    )

    diff = """\
diff --git a/app/example.py b/app/example.py
--- a/app/example.py
+++ b/app/example.py
@@ -1 +1 @@
-old_value = 1
+new_value = 1
"""

    result = await agent.execute({"diff_content": diff})

    assert result.finding_count == 1
    assert result.findings[0].start_line is None
    assert result.findings[0].end_line is None
