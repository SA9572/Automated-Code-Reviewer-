import re
from collections.abc import Iterable

from app.schemas.agent_result import AgentResult

REDACTED = "[REDACTED]"

QUOTED_SECRET_PATTERN = re.compile(
    r"""(?i)\b(password|passwd|pwd|secret|client_secret|api[_-]?key|"""
    r"""access[_-]?token|refresh[_-]?token|auth[_-]?token)\b"""
    r"""(\s*[:=]\s*)(['"])(.*?)\3"""
)

UNQUOTED_SECRET_PATTERN = re.compile(
    r"""(?i)\b(password|passwd|pwd|secret|client_secret|api[_-]?key|"""
    r"""access[_-]?token|refresh[_-]?token|auth[_-]?token)\b"""
    r"""(\s*[:=]\s*)([^\s,;]+)"""
)

BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
    r"-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{5,}\."
    r"[A-Za-z0-9_-]{5,}\."
    r"[A-Za-z0-9_-]{5,}\b"
)
URL_CREDENTIAL_PATTERN = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")


def extract_sensitive_values(*texts: str | None) -> set[str]:
    """Extract credential values from supplied source text for exact redaction."""
    values: set[str] = set()

    for text in texts:
        if not isinstance(text, str) or not text:
            continue

        for match in QUOTED_SECRET_PATTERN.finditer(text):
            value = match.group(4).strip()
            if len(value) >= 4 and value != REDACTED:
                values.add(value)

        for match in UNQUOTED_SECRET_PATTERN.finditer(text):
            value = match.group(3).strip().strip("\"'")
            if len(value) >= 4 and value != REDACTED:
                values.add(value)

        for match in BEARER_PATTERN.finditer(text):
            token = match.group(0).split(maxsplit=1)[-1]
            if len(token) >= 4:
                values.add(token)

        for match in JWT_PATTERN.finditer(text):
            values.add(match.group(0))

    return values


def redact_text(
    value: str | None,
    sensitive_values: Iterable[str] = (),
) -> str | None:
    """Redact credential patterns and exact sensitive values."""

    if value is None:
        return None

    if not isinstance(value, str):
        return value

    value = PRIVATE_KEY_PATTERN.sub(REDACTED, value)

    value = QUOTED_SECRET_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}"
            f"{match.group(3)}{REDACTED}{match.group(3)}"
        ),
        value,
    )

    value = UNQUOTED_SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        value,
    )

    value = BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        value,
    )

    value = JWT_PATTERN.sub(REDACTED, value)

    value = URL_CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTED}{match.group(2)}",
        value,
    )

    # Replace known values wherever an agent repeats them in prose or code.
    for secret in sorted(set(sensitive_values), key=len, reverse=True):
        if secret and secret != REDACTED:
            value = value.replace(secret, REDACTED)

    return value


def redact_agent_result(
    result: AgentResult,
    sensitive_values: Iterable[str] = (),
) -> AgentResult:
    """Return a sanitized copy of an agent result."""

    secrets = tuple(sensitive_values)

    sanitized_findings = [
        finding.model_copy(
            update={
                "title": redact_text(finding.title, secrets),
                "description": redact_text(finding.description, secrets),
                "file_path": redact_text(finding.file_path, secrets),
                "evidence": redact_text(finding.evidence, secrets),
                "recommendation": redact_text(finding.recommendation, secrets),
            }
        )
        for finding in result.findings
    ]

    return result.model_copy(
        update={
            "summary": redact_text(result.summary, secrets),
            "error": redact_text(result.error, secrets),
            "findings": sanitized_findings,
        }
    )
