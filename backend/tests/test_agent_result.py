import pytest
from pydantic import ValidationError

from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity


def create_finding():

    return Finding(
        agent_name="SecurityAgent",
        title="Hardcoded secret detected",
        description="A secret is stored directly in source code.",
        severity=Severity.CRITICAL,
        confidence=0.99,
        file_path="config.py",
        start_line=10,
        evidence='API_KEY = "example-secret"',
        recommendation="Use environment variables.",
    )


def test_successful_agent_result():

    result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary="Security analysis completed.",
        findings=[create_finding()],
        execution_time_ms=1500,
    )

    assert result.is_successful is True
    assert result.finding_count == 1
    assert result.execution_time_ms == 1500


def test_agent_without_findings():

    result = AgentResult(
        agent_name="StyleAgent",
        status=AgentStatus.SUCCESS,
        summary="No style issues detected.",
    )

    assert result.finding_count == 0
    assert result.is_successful is True


def test_failed_agent_result():

    result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.FAILED,
        error="LLM request failed.",
    )

    assert result.is_successful is False
    assert result.status == AgentStatus.FAILED


def test_negative_execution_time():

    with pytest.raises(ValidationError):
        AgentResult(
            agent_name="DiffAgent", status=AgentStatus.SUCCESS, execution_time_ms=-100
        )


def test_invalid_status():

    with pytest.raises(ValidationError):
        AgentResult(agent_name="DiffAgent", status="UNKNOWN")
