"""Authenticated GitHub REST API client with bounded error handling."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Base error for GitHub integration failures."""


class GitHubNotConfiguredError(GitHubError):
    """Raised when no server-side GitHub token is configured."""


class GitHubAPIError(GitHubError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class GitHubRateLimitError(GitHubAPIError):
    def __init__(self, status_code: int, message: str, retry_after: int | None = None):
        self.retry_after = retry_after
        super().__init__(status_code, message)


class GitHubClient:
    """Small async client for the GitHub REST API.

    A fine-grained PAT or GitHub App token may be supplied through
    GITHUB_TOKEN. The token is never returned by this service.
    """

    def __init__(self, settings: Settings | None = None, transport=None):
        self.settings = settings or get_settings()
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.GITHUB_TOKEN
            and self.settings.GITHUB_TOKEN.get_secret_value().strip()
        )

    def _require_token(self) -> str:
        if not self.is_configured:
            raise GitHubNotConfiguredError(
                "GitHub integration is not configured. Set GITHUB_TOKEN on the backend."
            )
        return self.settings.GITHUB_TOKEN.get_secret_value()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.GITHUB_API_URL.rstrip("/") + "/",
                timeout=self.settings.GITHUB_TIMEOUT_SECONDS,
                follow_redirects=True,
                transport=self._transport,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._require_token()}",
                    "X-GitHub-Api-Version": self.settings.GITHUB_API_VERSION,
                    "User-Agent": "Automated-Code-Review-Board",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _rate_limit_delay(response: httpx.Response) -> int | None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0, int(float(retry_after)))
            except ValueError:
                pass
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = response.headers.get("x-ratelimit-reset")
            try:
                return max(0, int(float(reset)) - int(time.time()))
            except (TypeError, ValueError):
                return None
        return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        accept: str | None = None,
        raw: bool = False,
    ) -> Any:
        client = await self._get_client()
        headers = {"Accept": accept} if accept else None
        attempts = self.settings.GITHUB_MAX_RETRIES + 1

        for attempt in range(attempts):
            try:
                response = await client.request(
                    method, path.lstrip("/"), params=params, json=json, headers=headers
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
                    continue
                raise GitHubAPIError(503, "GitHub could not be reached.") from exc

            if response.status_code in {403, 429}:
                message = "GitHub rate limit or permission restriction."
                try:
                    payload = response.json()
                    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
                        message = payload["message"][:300]
                except ValueError:
                    pass
                retry_after = self._rate_limit_delay(response)
                is_rate_limited = (
                    response.status_code == 429
                    or response.headers.get("x-ratelimit-remaining") == "0"
                    or "rate limit" in message.lower()
                )
                if is_rate_limited:
                    # Honor short server-provided waits, but never hold an API
                    # request open for a long primary/secondary reset window.
                    max_wait = self.settings.GITHUB_MAX_RATE_LIMIT_WAIT_SECONDS
                    if (
                        retry_after is not None
                        and retry_after <= max_wait
                        and attempt + 1 < attempts
                    ):
                        await asyncio.sleep(retry_after)
                        continue
                    raise GitHubRateLimitError(
                        response.status_code, message, retry_after
                    )
                raise GitHubAPIError(response.status_code, message)

            if response.status_code >= 500 and attempt + 1 < attempts:
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
                continue

            if response.is_error:
                try:
                    payload = response.json()
                    message = payload.get("message", "GitHub request failed.")
                except (ValueError, AttributeError):
                    message = "GitHub request failed."
                raise GitHubAPIError(response.status_code, str(message)[:300])

            if raw:
                return response.text
            if response.status_code == 204:
                return None
            return response.json()

        raise GitHubAPIError(503, "GitHub request could not be completed.")

    async def get_authenticated_user(self) -> dict[str, Any]:
        return await self._request("GET", "/user")

    async def list_repositories(
        self, *, per_page: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/user/repos",
            params={"per_page": per_page, "page": page, "sort": "updated"},
        )

    async def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        """Retrieve repository metadata, including its default branch."""
        return await self._request("GET", f"/repos/{owner}/{repo}")

    async def get_branch_commit(
        self, owner: str, repo: str, branch: str
    ) -> dict[str, Any]:
        """Resolve a branch or tag to its current commit."""
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/commits/{branch}"
        )

    async def get_git_tree(
        self, owner: str, repo: str, tree_sha: str
    ) -> dict[str, Any]:
        """Retrieve a recursive Git tree for a commit."""
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )

    async def get_git_blob(
        self, owner: str, repo: str, blob_sha: str
    ) -> dict[str, Any]:
        """Retrieve a Git blob (GitHub returns base64-encoded content)."""
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/git/blobs/{blob_sha}"
        )

    async def list_pull_requests(
        self, owner: str, repo: str, *, state: str = "open",
        per_page: int = 100, page: int = 1,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": per_page, "page": page},
        )

    async def get_pull_request(
        self, owner: str, repo: str, pull_number: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}"
        )

    async def get_pull_request_diff(
        self, owner: str, repo: str, pull_number: int
    ) -> str:
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
            accept="application/vnd.github.v3.diff",
            raw=True,
        )

    async def list_pull_request_files(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        max_files = self.settings.GITHUB_MAX_FILES_PER_PR

        while True:
            batch = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{pull_number}/files",
                params={"per_page": per_page, "page": page},
            )
            if not isinstance(batch, list):
                raise GitHubAPIError(502, "GitHub returned an invalid changed-files response.")
            files.extend(batch)
            if len(files) > max_files:
                raise GitHubAPIError(
                    413,
                    f"Pull request changes more than the configured limit of {max_files} files.",
                )
            if len(batch) < per_page:
                return files
            page += 1

    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        *,
        commit_id: str,
        body: str,
        event: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
        }
        if comments:
            payload["comments"] = comments
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            json=payload,
        )
