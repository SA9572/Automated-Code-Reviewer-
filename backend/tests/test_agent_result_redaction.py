import pytest

from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.security.secret_redactor import REDACTED, redact_agent_result


@pytest.fixture
def agent_result():
    finding = Finding(
        agent_name="SecurityAgent",
        title='Hardcoded password="admin123" detected',
        description='The source contains password="admin123".',
        severity=Severity.HIGH,
        confidence=0.98,
        file_path="backend/config.py",
        start_line=10,
        end_line=10,
        evidence='password="admin123"',
        recommendation='Replace password="admin123" with an environment variable.',
        rule_id="SEC001",
    )

    return AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary='Found password="admin123" in source code.',
        findings=[finding],
        execution_time_ms=120,
        error='Diagnostic context: password="admin123"',
    )


def test_redacts_agent_summary(agent_result):
    result = redact_agent_result(agent_result)

    assert REDACTED in result.summary
    assert "admin123" not in result.summary


def test_redacts_agent_error(agent_result):
    result = redact_agent_result(agent_result)

    assert REDACTED in result.error
    assert "admin123" not in result.error


def test_redacts_finding_title(agent_result):
    result = redact_agent_result(agent_result)

    assert REDACTED in result.findings[0].title
    assert "admin123" not in result.findings[0].title


def test_redacts_finding_description(agent_result):
    result = redact_agent_result(agent_result)

    assert REDACTED in result.findings[0].description
    assert "admin123" not in result.findings[0].description


def test_redacts_finding_evidence(agent_result):
    result = redact_agent_result(agent_result)

    assert REDACTED in result.findings[0].evidence
    assert "admin123" not in result.findings[0].evidence


def test_redacts_finding_recommendation(agent_result):
    result = redact_agent_result(agent_result)

    assert REDACTED in result.findings[0].recommendation
    assert "admin123" not in result.findings[0].recommendation


def test_redacts_finding_file_path(agent_result):
    agent_result.findings[0].file_path = 'backend/password="admin123"/config.py'

    result = redact_agent_result(agent_result)

    assert REDACTED in result.findings[0].file_path
    assert "admin123" not in result.findings[0].file_path


def test_preserves_non_sensitive_fields(agent_result):
    result = redact_agent_result(agent_result)

    assert result.agent_name == "SecurityAgent"
    assert result.status == AgentStatus.SUCCESS
    assert result.execution_time_ms == 120
    assert result.findings[0].severity == Severity.HIGH
    assert result.findings[0].rule_id == "SEC001"


def test_does_not_modify_original_agent_result(agent_result):
    original_summary = agent_result.summary
    original_title = agent_result.findings[0].title

    sanitized = redact_agent_result(agent_result)

    assert agent_result.summary == original_summary
    assert agent_result.findings[0].title == original_title

    assert "admin123" not in sanitized.summary
    assert "admin123" not in sanitized.findings[0].title


def test_handles_agent_result_without_error():
    result = AgentResult(
        agent_name="StyleAgent",
        status=AgentStatus.SUCCESS,
        summary="No style issues found.",
    )

    sanitized = redact_agent_result(result)

    assert sanitized.error is None
    assert sanitized.findings == []
    assert sanitized.summary == "No style issues found."
