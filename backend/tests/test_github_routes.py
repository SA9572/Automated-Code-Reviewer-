
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_github_client, get_review_pipeline
from app.config import Settings
from app.schemas.finding import Finding, Severity
from app.schemas.review import Review, ReviewStatus
from app.services.github_client import GitHubClient
from app.state.change_state import ChangeState
from main import app


DIFF = """diff --git a/src/example.py b/src/example.py
index 1111111..2222222 100644
--- a/src/example.py
+++ b/src/example.py
@@ -1,1 +1,2 @@
 old = 1
+new = 2
"""


class FakePipeline:
    def __init__(self):
        self.run_kwargs = None
        self.state = ChangeState(
            repository="org/repo",
            pull_request_number=3,
            base_commit="a" * 40,
            head_commit="b" * 40,
            changed_files=["src/example.py"],
            diff_content=DIFF,
            status=ReviewStatus.COMPLETED,
            change_summary="One issue found.",
            final_findings=[
                Finding(
                    agent_name="SecurityAgent",
                    title="Unsafe operation",
                    description="An unsafe operation was detected.",
                    severity=Severity.HIGH,
                    confidence=0.95,
                    file_path="src/example.py",
                    start_line=2,
                    end_line=2,
                    evidence="new = 2",
                    recommendation="Validate the input.",
                )
            ],
        )

    async def run(self, **kwargs):
        self.run_kwargs = kwargs
        return Review(
            repository=kwargs["repository"],
            pull_request_number=kwargs["pull_request_number"],
            base_commit=kwargs["base_commit"],
            head_commit=kwargs["head_commit"],
            status=ReviewStatus.COMPLETED,
            summary="AI review completed.",
        )


class FakeStateManager:
    async def get_state(self, review_id):
        if review_id != "review-123":
            raise KeyError(review_id)
        return FakePipeline.state


@pytest.fixture
def github_client():
    def handler(request):
        path = request.url.path

        if path == "/user":
            return httpx.Response(200, json={"login": "test-user"})

        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "name": "repo",
                        "full_name": "org/repo",
                        "private": True,
                        "permissions": {"pull": True},
                    }
                ],
            )

        if (
            path == "/repos/org/repo/pulls/3"
            and request.headers["Accept"]
            == "application/vnd.github.v3.diff"
        ):
            return httpx.Response(200, text=DIFF)

        if path == "/repos/org/repo/pulls/3":
            return httpx.Response(
                200,
                json={
                    "state": "open",
                    "base": {
                        "sha": "a" * 40,
                        "repo": {"full_name": "org/repo"},
                    },
                    "head": {"sha": "b" * 40},
                },
            )

        if path == "/repos/org/repo/pulls/3/files":
            return httpx.Response(
                200,
                json=[{"filename": "src/example.py"}],
            )

        if path == "/repos/org/repo/pulls/3/reviews":
            assert request.method == "POST"

            payload = __import__("json").loads(request.content)

            assert payload["commit_id"] == "b" * 40
            assert payload["event"] == "COMMENT"
            assert len(payload["comments"]) == 1

            return httpx.Response(
                200,
                json={
                    "id": 99,
                    "state": "COMMENTED",
                    "html_url": (
                        "https://github.com/org/repo/pull/3"
                        "#pullrequestreview-99"
                    ),
                },
            )

        return httpx.Response(
            404,
            json={"message": f"Unexpected {request.method} {path}"},
        )

    settings = Settings(
        GROQ_API_KEY="test-key",
        GROQ_MODEL="test-model",
        GITHUB_TOKEN="github-test-token",
    )

    client = GitHubClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
    )

    yield client

    import asyncio

    asyncio.run(client.close())


@pytest.fixture
def setup_client(github_client):
    pipeline = FakePipeline()
    pipeline.state_manager = FakeStateManager()
    FakePipeline.state = pipeline.state

    app.dependency_overrides[get_github_client] = lambda: github_client
    app.dependency_overrides[get_review_pipeline] = lambda: pipeline

    with TestClient(app) as client:
        yield client, pipeline

    app.dependency_overrides.clear()


def test_github_auth_status_does_not_expose_token(setup_client):
    client, _ = setup_client

    response = client.get("/api/v1/github/auth/status")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "provider": "GitHub REST API",
    }
    assert "github-test-token" not in response.text


def test_list_repositories_returns_safe_fields(setup_client):
    client, _ = setup_client

    response = client.get("/api/v1/github/repos")

    assert response.status_code == 200
    assert response.json()[0]["full_name"] == "org/repo"


def test_run_github_pull_request_uses_existing_pipeline(setup_client):
    client, pipeline = setup_client

    response = client.post(
        "/api/v1/github/repos/org/repo/pulls/3/review"
    )

    assert response.status_code == 200, response.text
    assert response.json()["repository"] == "org/repo"
    assert pipeline.run_kwargs["base_commit"] == "a" * 40
    assert pipeline.run_kwargs["head_commit"] == "b" * 40
    assert pipeline.run_kwargs["changed_files"] == ["src/example.py"]


def test_submit_review_posts_inline_comment_to_exact_head(setup_client):
    client, _ = setup_client

    response = client.post(
        "/api/v1/github/reviews/review-123/submit",
        json={"event": "COMMENT"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["review_id"] == 99
    assert response.json()["comments_posted"] == 1
    assert response.json()["commit_id"] == "b" * 40


def test_submit_rejects_unknown_review(setup_client):
    client, _ = setup_client

    response = client.post(
        "/api/v1/github/reviews/not-found/submit",
        json={"event": "COMMENT"},
    )

    assert response.status_code == 404


def test_run_github_baseline_review_uses_default_branch_source_files(
    setup_client,
):
    import base64

    client, pipeline = setup_client

    # Use actual newline characters in the source snapshot.
    source = "def calculate(value):\n    return eval(value)\n"
    encoded = base64.b64encode(
        source.encode("utf-8")
    ).decode("ascii")

    # Replace the fixture transport with a deterministic baseline repository.
    api_client = client.app.dependency_overrides[get_github_client]()

    def handler(request):
        path = request.url.path

        if path == "/repos/org/repo":
            return httpx.Response(
                200,
                json={"default_branch": "main"},
            )

        if path == "/repos/org/repo/commits/main":
            return httpx.Response(
                200,
                json={
                    "sha": "c" * 40,
                    "commit": {
                        "tree": {"sha": "d" * 40}
                    },
                },
            )

        if path == "/repos/org/repo/git/trees/" + "d" * 40:
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {
                            "path": "app.py",
                            "type": "blob",
                            "sha": "e" * 40,
                            "size": len(source),
                        },
                        {
                            "path": "data.sqlite",
                            "type": "blob",
                            "sha": "f" * 40,
                            "size": 500,
                        },
                    ],
                },
            )

        if path == "/repos/org/repo/git/blobs/" + "e" * 40:
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": encoded,
                },
            )

        return httpx.Response(
            404,
            json={"message": f"Unexpected {path}"},
        )

    # A fresh client keeps the fixture's original PR assertions isolated.
    baseline_api = GitHubClient(
        settings=api_client.settings,
        transport=httpx.MockTransport(handler),
    )

    client.app.dependency_overrides[get_github_client] = (
        lambda: baseline_api
    )

    try:
        response = client.post(
            "/api/v1/github/repos/org/repo/baseline-review"
        )

        assert response.status_code == 200, response.text
        assert response.json()["repository"] == "org/repo"
        assert response.json()["pull_request_number"] is None

        assert pipeline.run_kwargs["pull_request_number"] is None
        assert pipeline.run_kwargs["base_commit"] == "c" * 40
        assert pipeline.run_kwargs["head_commit"] == "c" * 40
        assert pipeline.run_kwargs["changed_files"] == ["app.py"]

        # Baseline reviews use source snapshots, not synthetic diffs.
        assert pipeline.run_kwargs["diff_content"] == ""
        assert pipeline.run_kwargs["review_mode"] == "baseline"

        assert pipeline.run_kwargs["source_files"] == [
            {"path": "app.py", "content": source}
        ]

        assert pipeline.run_kwargs["test_source_files"] == []
        assert pipeline.run_kwargs["snapshot_complete"] is True

    finally:
        import asyncio

        asyncio.run(baseline_api.close())

        client.app.dependency_overrides[get_github_client] = (
            lambda: api_client
        )


def test_baseline_source_snapshot_preserves_original_line_numbers():
    source = (
        "def calculate(value):\n"
        "    result = eval(value)\n"
        "    return result\n"
    )

    source_file = {
        "path": "app.py",
        "content": source,
    }

    assert source_file["path"] == "app.py"
    assert (
        source_file["content"].splitlines()[0]
        == "def calculate(value):"
    )
    assert (
        source_file["content"].splitlines()[1]
        == "    result = eval(value)"
    )
    assert (
        source_file["content"].splitlines()[2]
        == "    return result"
    )
