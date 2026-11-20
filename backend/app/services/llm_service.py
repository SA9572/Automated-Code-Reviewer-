from agent_framework.openai import OpenAIChatCompletionClient

from app.config import Settings, get_settings


class LLMService:
    """
    Centralized LLM client management.

    All specialist agents will use this service.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None

    def get_client(self) -> OpenAIChatCompletionClient:
        """Return the shared OpenAI-compatible Groq client."""

        if self._client is None:
            self._client = OpenAIChatCompletionClient(
                api_key=self.settings.GROQ_API_KEY.get_secret_value(),
                base_url=self.settings.GROQ_BASE_URL,
                model=self.settings.GROQ_MODEL,
            )

        return self._client

    async def close(self):
        """Close the LLM client and reset the cached instance."""

        if self._client is not None:
            await self._client.close()
            self._client = None
