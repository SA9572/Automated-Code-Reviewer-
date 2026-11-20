from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Automated Code Review Board"

    APP_ENV: str = "development"

    DEBUG: bool = True

    GROQ_API_KEY: SecretStr

    GROQ_MODEL: str

    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    # GitHub REST API integration. Keep the token server-side only.
    GITHUB_TOKEN: SecretStr | None = None
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_API_VERSION: str = "2026-03-10"
    GITHUB_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    GITHUB_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    GITHUB_MAX_RATE_LIMIT_WAIT_SECONDS: int = Field(default=5, ge=0, le=30)
    GITHUB_MAX_FILES_PER_PR: int = Field(default=300, ge=1, le=3000)
    GITHUB_MAX_DIFF_BYTES: int = Field(default=5_000_000, ge=1024)

    LLM_TIMEOUT_SECONDS: float = Field(
        default=60.0,
        gt=0,
        description="Maximum execution time per agent attempt, in seconds",
    )

    MAX_RETRIES: int = 2

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
