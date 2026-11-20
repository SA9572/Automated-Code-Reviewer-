import pytest
from pydantic import ValidationError

from app.schemas.finding import Finding, Severity


def make_finding(**overrides):
    data = {
        "agent_name": "SecurityAgent",
        "title": "Hardcoded API key detected",
        "description": "An API key is directly stored in source code.",
        "severity": Severity.CRITICAL,
        "confidence": 0.99,
        "file_path": "src/config.py",
        "start_line": 10,
        "end_line": 10,
        "evidence": 'API_KEY = "example-secret"',
        "recommendation": "Move the secret into a secure environment variable.",
    }
    data.update(overrides)
    return Finding(**data)


def test_valid_finding():
    finding = make_finding()

    assert finding.severity == Severity.CRITICAL
    assert finding.is_blocking is True
    assert finding.confidence == 0.99


def test_invalid_confidence():
    with pytest.raises(ValidationError):
        make_finding(confidence=1.5)


def test_unique_finding_ids():
    common = {
        "agent_name": "StyleAgent",
        "title": "Unused variable",
        "description": "Unused variable detected.",
        "severity": Severity.LOW,
        "confidence": 0.90,
        "file_path": "main.py",
        "evidence": "unused_variable = 10",
        "recommendation": "Remove the unused variable.",
    }

    first = Finding(**common)
    second = Finding(**common)

    assert first.finding_id != second.finding_id


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [
        (25, 10),
        (100, 99),
        (1, 0),
        (10, -1),
    ],
)
def test_invalid_line_ranges(start_line, end_line):
    with pytest.raises(ValidationError):
        make_finding(
            start_line=start_line,
            end_line=end_line,
        )


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [
        (10, 10),
        (10, 15),
        (1, 100),
    ],
)
def test_valid_line_ranges(start_line, end_line):
    finding = make_finding(
        start_line=start_line,
        end_line=end_line,
    )

    assert finding.start_line == start_line
    assert finding.end_line == end_line


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [
        (None, None),
        (10, None),
        (None, 15),
    ],
)
def test_partial_or_missing_line_ranges_are_allowed(
    start_line,
    end_line,
):
    finding = make_finding(
        start_line=start_line,
        end_line=end_line,
    )

    assert finding.start_line == start_line
    assert finding.end_line == end_line


def test_line_range_assignment_validation():
    finding = make_finding(
        start_line=10,
        end_line=20,
    )

    with pytest.raises(ValidationError):
        finding.end_line = 5
