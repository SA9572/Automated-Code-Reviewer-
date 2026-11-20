import httpx
import pytest

from app.config import Settings
from app.services.github_client import (
    GitHubAPIError,
    GitHubClient,
    GitHubNotConfiguredError,
    GitHubRateLimitError,
)


def make_settings(**kwargs):
    return Settings(
        GROQ_API_KEY="groq-test-key",
        GROQ_MODEL="test-model",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_github_client_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    settings = Settings(
        GROQ_API_KEY="test-api-key",
        GROQ_MODEL="test-model",
        _env_file=None,
    )

    client = GitHubClient(settings=settings)

    with pytest.raises(GitHubNotConfiguredError):
        await client.get_authenticated_user()


@pytest.mark.asyncio
async def test_get_authenticated_user_sends_bearer_token_and_api_version():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer github-test-token"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        return httpx.Response(200, json={"login": "octocat"})

    client = GitHubClient(
        settings=make_settings(GITHUB_TOKEN="github-test-token"),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.get_authenticated_user() == {"login": "octocat"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_github_client_maps_unauthorized_response():
    client = GitHubClient(
        settings=make_settings(GITHUB_TOKEN="bad-token"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"message": "Bad credentials"})
        ),
    )
    try:
        with pytest.raises(GitHubAPIError) as exc:
            await client.get_authenticated_user()
        assert exc.value.status_code == 401
        assert "Bad credentials" in exc.value.message
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_github_rate_limit_returns_retry_guidance_without_sleeping():
    client = GitHubClient(
        settings=make_settings(GITHUB_TOKEN="token"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429,
                json={"message": "You have exceeded a secondary rate limit"},
                headers={"Retry-After": "60"},
            )
        ),
    )
    try:
        with pytest.raises(GitHubRateLimitError) as exc:
            await client.get_authenticated_user()
        assert exc.value.retry_after == 60
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pull_request_files_are_paginated():
    requested_pages = []

    def handler(request):
        page = int(request.url.params["page"])
        requested_pages.append(page)
        if page == 1:
            return httpx.Response(200, json=[{"filename": f"file-{i}.py"} for i in range(100)])
        return httpx.Response(200, json=[{"filename": "last.py"}])

    client = GitHubClient(
        settings=make_settings(GITHUB_TOKEN="token"),
        transport=httpx.MockTransport(handler),
    )
    try:
        files = await client.list_pull_request_files("owner", "repo", 7)
        assert len(files) == 101
        assert requested_pages == [1, 2]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pull_request_diff_uses_diff_media_type():
    def handler(request):
        assert request.headers["Accept"] == "application/vnd.github.v3.diff"
        return httpx.Response(200, text="diff --git a/a.py b/a.py\n")

    client = GitHubClient(
        settings=make_settings(GITHUB_TOKEN="token"),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert "diff --git" in await client.get_pull_request_diff("owner", "repo", 1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_short_retry_after_is_honored_with_bounded_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                json={"message": "secondary rate limit"},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json={"login": "octocat"})

    client = GitHubClient(
        settings=make_settings(GITHUB_TOKEN="token", GITHUB_MAX_RETRIES=1),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.get_authenticated_user() == {"login": "octocat"}
        assert calls == 2
    finally:
        await client.close()



