import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_review_pipeline
from app.orchestration.review_pipeline import ReviewPipeline
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.state.state_manager import StateManager
from main import app


class MockAgent:
    """Mock specialist agent for API tests."""

    def __init__(self, agent_name, findings=None):
        self.agent_name = agent_name
        self.findings = findings or []

    async def execute(self, context):
        return AgentResult(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary="Mock analysis completed.",
            findings=self.findings,
            execution_time_ms=10,
        )


def make_finding():
    return Finding(
        agent_name="DiffAgent",
        title="Potential security issue",
        description="An unsafe operation was detected.",
        severity=Severity.HIGH,
        confidence=0.95,
        file_path="app/main.py",
        evidence="Unsafe user input handling",
        recommendation="Validate user input.",
    )


@pytest.fixture
def client():
    state_manager = StateManager()

    pipeline = ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent", [make_finding()]),
            MockAgent("StyleAgent"),
            MockAgent("SecurityAgent"),
            MockAgent("TestCoverageAgent"),
        ],
    )

    app.dependency_overrides[get_review_pipeline] = lambda: pipeline

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def valid_review_payload():
    return {
        "repository": "test/repository",
        "base_commit": "abc123",
        "head_commit": "def456",
        "diff_content": (
            "--- a/app/main.py\n"
            "+++ b/app/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old_code()\n"
            "+new_code()\n"
        ),
        "changed_files": ["app/main.py"],
        "pull_request_number": 10,
    }


def test_health_endpoint(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root_endpoint(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["message"] == ("Automated Code Review Board API")


def test_create_review(client):
    response = client.post(
        "/api/v1/reviews",
        json=valid_review_payload(),
    )

    assert response.status_code == 200

    data = response.json()

    assert data["repository"] == "test/repository"
    assert data["status"] == "COMPLETED"
    assert data["recommendation"] == "REQUEST_CHANGES"
    assert len(data["agent_results"]) == 4
    assert len(data["findings"]) == 1

    assert data["review_id"]


def test_get_review(client):
    create_response = client.post(
        "/api/v1/reviews",
        json=valid_review_payload(),
    )

    review_id = create_response.json()["review_id"]

    response = client.get(f"/api/v1/reviews/{review_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["review_id"] == review_id
    assert data["status"] == "COMPLETED"
    assert data["recommendation"] == "REQUEST_CHANGES"


def test_get_nonexistent_review(client):
    response = client.get("/api/v1/reviews/nonexistent-review-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "Review not found."


def test_create_review_validation_error(client):
    payload = valid_review_payload()
    payload["repository"] = ""

    response = client.post(
        "/api/v1/reviews",
        json=payload,
    )

    assert response.status_code == 422


def test_create_review_preserves_unicode_in_api_response(client):
    payload = valid_review_payload()
    payload["repository"] = "org/café-日本-नमस्ते"

    response = client.post(
        "/api/v1/reviews",
        json=payload,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["repository"] == "org/café-日本-नमस्ते"
