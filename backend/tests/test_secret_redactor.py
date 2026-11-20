from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.security.secret_redactor import (
    REDACTED,
    extract_sensitive_values,
    redact_agent_result,
    redact_text,
)


def test_extract_secret_from_quoted_assignment():
    secrets = extract_sensitive_values('API_KEY = "hardcoded-secret-example"')

    assert "hardcoded-secret-example" in secrets


def test_exact_secret_is_redacted_from_prose():
    secret = "hardcoded-secret-example"

    result = redact_text(
        f'The API key "{secret}" is embedded in the source.',
        {secret},
    )

    assert secret not in result
    assert REDACTED in result


def test_secret_is_redacted_from_all_finding_fields():
    secret = "hardcoded-secret-example"

    finding = Finding(
        agent_name="SecurityAgent",
        title=f"Exposed key: {secret}",
        description=f"The key {secret} is embedded in source.",
        severity=Severity.HIGH,
        confidence=0.99,
        file_path="app/auth.py",
        evidence=f'API_KEY = "{secret}"',
        recommendation=f"Rotate {secret} immediately.",
        rule_id="SEC003",
    )

    result = AgentResult(
        agent_name="SecurityAgent",
        status=AgentStatus.SUCCESS,
        summary=f"Found credential {secret}.",
        findings=[finding],
    )

    sanitized = redact_agent_result(result, {secret})
    sanitized_finding = sanitized.findings[0]

    for value in (
        sanitized.summary,
        sanitized_finding.title,
        sanitized_finding.description,
        sanitized_finding.evidence,
        sanitized_finding.recommendation,
    ):
        assert secret not in value
        assert REDACTED in value
