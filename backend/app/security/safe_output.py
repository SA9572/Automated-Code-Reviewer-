import re

UNSAFE_COMMAND_PATTERNS = [
    re.compile(
        r"""__import__\s*\(\s*['"]os['"]\s*\)\s*\.\s*system\s*\(""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""\bos\s*\.\s*system\s*\(""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""\bsubprocess\s*\.\s*(run|Popen|call|check_call|check_output)\s*\(""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""\brm\s+-rf\s+(?:/|~|\*)""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""\bmkfs(?:\.[a-z0-9]+)?\b|\bdd\s+if=""",
        re.IGNORECASE,
    ),
]

SAFE_REPLACEMENT = (
    "Unsafe command example omitted. Use harmless test inputs, "
    "isolated fixtures, and mocks instead."
)


def sanitize_unsafe_command_text(value: str | None) -> str | None:
    """Replace generated text containing unsafe command examples."""

    if value is None or not isinstance(value, str):
        return value

    if any(pattern.search(value) for pattern in UNSAFE_COMMAND_PATTERNS):
        return SAFE_REPLACEMENT

    return value
