from app.config import Settings


def test_settings_creation():

    settings = Settings(GROQ_API_KEY="test-api-key", GROQ_MODEL="test-model")

    assert settings.APP_NAME == "Automated Code Review Board"

    assert settings.GROQ_MODEL == "test-model"

    assert settings.GROQ_BASE_URL == "https://api.groq.com/openai/v1"


def test_api_key_is_secret():

    settings = Settings(GROQ_API_KEY="test-api-key", GROQ_MODEL="test-model")

    assert "test-api-key" not in str(settings.GROQ_API_KEY)


def test_custom_timeout():

    settings = Settings(
        GROQ_API_KEY="test-api-key", GROQ_MODEL="test-model", LLM_TIMEOUT_SECONDS=30
    )

    assert settings.LLM_TIMEOUT_SECONDS == 30


def test_github_settings_are_optional_and_token_is_secret(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    settings = Settings(
        GROQ_API_KEY="test-api-key",
        GROQ_MODEL="test-model",
        _env_file=None,
    )

    assert settings.GITHUB_TOKEN is None


def test_github_token_is_stored_as_secret():
    settings = Settings(
        GROQ_API_KEY="test-api-key",
        GROQ_MODEL="test-model",
        GITHUB_TOKEN="github-test-token",
    )
    assert settings.GITHUB_TOKEN.get_secret_value() == "github-test-token"
    assert "github-test-token" not in str(settings.GITHUB_TOKEN)
