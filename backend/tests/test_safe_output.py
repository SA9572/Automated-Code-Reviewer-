import pytest

from app.security.safe_output import (
    SAFE_REPLACEMENT,
    sanitize_unsafe_command_text,
)


@pytest.mark.parametrize(
    "value",
    [
        "__import__('os').system('rm -rf /')",
        "os.system('some command')",
        "subprocess.run('some command', shell=True)",
        "Recommendation: rm -rf /",
        "Use mkfs to erase a disk",
    ],
)
def test_unsafe_command_examples_are_replaced(value):
    result = sanitize_unsafe_command_text(value)

    assert result == SAFE_REPLACEMENT
    assert "rm -rf /" not in result
    assert "os.system" not in result
    assert "subprocess.run" not in result


@pytest.mark.parametrize(
    "value",
    [
        "Test that eval is removed from the implementation.",
        "Use harmless inputs and mocks.",
        "Verify invalid input is rejected.",
        "Test that arbitrary expressions are never evaluated.",
        "The application should not execute untrusted code.",
    ],
)
def test_safe_security_recommendations_are_preserved(value):
    assert sanitize_unsafe_command_text(value) == value


def test_none_is_preserved():
    assert sanitize_unsafe_command_text(None) is None


def test_unicode_is_preserved():
    value = "Test Unicode: café — नमस्ते — 中文"

    assert sanitize_unsafe_command_text(value) == value
