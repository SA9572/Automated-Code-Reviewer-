import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_review_pipeline
from app.api.routes import health_router, reviews_router
from app.database import models as database_models  # noqa: F401
from app.database.base import Base
from app.orchestration.review_pipeline import ReviewPipeline
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding, Severity
from app.schemas.review import MergeRecommendation
from app.state.state_manager import StateManager


class MockAgent:
    """Deterministic specialist agent for API integration tests."""

    def __init__(self, agent_name: str, findings: list[Finding] | None = None):
        self.agent_name = agent_name
        self.findings = findings or []

    async def execute(self, context):
        return AgentResult(
            agent_name=self.agent_name,
            status=AgentStatus.SUCCESS,
            summary="Mock analysis completed.",
            findings=self.findings,
            execution_time_ms=5,
        )


def make_finding() -> Finding:
    return Finding(
        agent_name="SecurityAgent",
        title="Potential unsafe input handling",
        description="User-controlled input should be validated.",
        severity=Severity.HIGH,
        confidence=0.95,
        file_path="app/main.py",
        start_line=10,
        end_line=10,
        evidence="An unsafe input operation was detected.",
        recommendation="Validate and sanitize user input.",
        rule_id="TEST-SEC-001",
    )


def make_pipeline(session_factory) -> ReviewPipeline:
    """Build a fresh pipeline backed by the supplied test database."""

    state_manager = StateManager(session_factory=session_factory)
    return ReviewPipeline(
        state_manager=state_manager,
        agents=[
            MockAgent("DiffAgent"),
            MockAgent("StyleAgent"),
            MockAgent("SecurityAgent", [make_finding()]),
            MockAgent("TestCoverageAgent"),
        ],
    )


def valid_review_payload() -> dict:
    return {
        "repository": "integration/test-repository",
        "base_commit": "base123",
        "head_commit": "head456",
        "diff_content": (
            "--- a/app/main.py\n"
            "+++ b/app/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old_code()\n"
            "+new_code()\n"
        ),
        "changed_files": ["app/main.py"],
        "pull_request_number": 42,
    }


@pytest.fixture
def api_environment(tmp_path: Path):
    """
    Create an isolated FastAPI application and temporary SQLite database.

    The test app deliberately has no production lifespan, so these tests
    never initialize, modify, or dispose the project's real reviews.db.
    """

    database_path = tmp_path / "api-integration.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"

    engine = create_async_engine(
        database_url,
        connect_args={"timeout": 30},
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )

    async def initialize_database():
        # Importing the models registers all tables on Base.metadata.
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(initialize_database())

    pipeline_holder = {"pipeline": make_pipeline(session_factory)}

    test_app = FastAPI()
    test_app.include_router(health_router, prefix="/api/v1")
    test_app.include_router(reviews_router, prefix="/api/v1")
    test_app.dependency_overrides[get_review_pipeline] = lambda: pipeline_holder[
        "pipeline"
    ]

    try:
        with TestClient(test_app) as client:
            yield client, pipeline_holder, session_factory
    finally:
        test_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_integration_health_endpoint(api_environment):
    client, _, _ = api_environment

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "Automated Code Review Board",
    }


def test_integration_create_review_persists_all_agent_results(api_environment):
    client, _, _ = api_environment

    response = client.post(
        "/api/v1/reviews",
        json=valid_review_payload(),
    )

    assert response.status_code == 200
    data = response.json()

    assert data["review_id"]
    assert data["repository"] == "integration/test-repository"
    assert data["status"] == "COMPLETED"
    assert len(data["agent_results"]) == 4
    assert len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "HIGH"
    assert data["recommendation"] == "REQUEST_CHANGES"


def test_integration_review_survives_new_state_manager(api_environment):
    """
    Verify persisted review retrieval using a new StateManager instance.

    This simulates the important persistence boundary across application
    service reconstruction without relying on the production database.
    """

    client, pipeline_holder, session_factory = api_environment

    create_response = client.post(
        "/api/v1/reviews",
        json=valid_review_payload(),
    )
    assert create_response.status_code == 200
    created = create_response.json()
    review_id = created["review_id"]

    # Replace the pipeline with a new one. It has no in-memory copy of the
    # review; it can retrieve the review only from the same SQLite database.
    pipeline_holder["pipeline"] = make_pipeline(session_factory)

    get_response = client.get(f"/api/v1/reviews/{review_id}")

    assert get_response.status_code == 200
    retrieved = get_response.json()

    assert retrieved["review_id"] == review_id
    assert retrieved["repository"] == created["repository"]
    assert retrieved["status"] == "COMPLETED"
    assert retrieved["recommendation"] == "REQUEST_CHANGES"
    assert len(retrieved["agent_results"]) == 4
    assert len(retrieved["findings"]) == 1


def test_integration_get_nonexistent_review_returns_404(api_environment):
    client, _, _ = api_environment

    response = client.get("/api/v1/reviews/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["detail"] == "Review not found."


@pytest.mark.parametrize(
    "payload_change",
    [
        {"repository": ""},
        {"base_commit": ""},
        {"head_commit": ""},
        {"diff_content": None},
    ],
)
def test_integration_create_review_rejects_invalid_payload(
    api_environment,
    payload_change,
):
    client, _, _ = api_environment
    payload = valid_review_payload()
    payload.update(payload_change)

    response = client.post("/api/v1/reviews", json=payload)

    assert response.status_code == 422


def test_integration_create_review_rejects_missing_required_fields(api_environment):
    client, _, _ = api_environment

    response = client.post(
        "/api/v1/reviews",
        json={"repository": "integration/test-repository"},
    )

    assert response.status_code == 422


def test_integration_list_reviews_returns_paginated_results(api_environment):
    client, _, _ = api_environment

    for index in range(3):
        payload = valid_review_payload()
        payload["head_commit"] = f"head-{index}"
        response = client.post("/api/v1/reviews", json=payload)
        assert response.status_code == 200

    response = client.get("/api/v1/reviews?skip=0&limit=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 3
    assert data["skip"] == 0
    assert data["limit"] == 2
    assert data["has_more"] is True


def test_integration_list_reviews_second_page_has_no_more_results(api_environment):
    client, _, _ = api_environment

    for index in range(3):
        payload = valid_review_payload()
        payload["head_commit"] = f"page-head-{index}"
        response = client.post("/api/v1/reviews", json=payload)
        assert response.status_code == 200

    response = client.get("/api/v1/reviews?skip=2&limit=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["total"] == 3
    assert data["has_more"] is False


def test_integration_list_reviews_filters_by_repository(api_environment):
    client, _, _ = api_environment

    first_payload = valid_review_payload()
    first_payload["repository"] = "org/first-repository"
    second_payload = valid_review_payload()
    second_payload["repository"] = "org/second-repository"

    assert client.post("/api/v1/reviews", json=first_payload).status_code == 200
    assert client.post("/api/v1/reviews", json=second_payload).status_code == 200

    response = client.get(
        "/api/v1/reviews",
        params={"repository": "org/first-repository"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["repository"] == "org/first-repository"


def test_integration_list_reviews_filters_by_status(api_environment):
    client, _, _ = api_environment

    created = client.post(
        "/api/v1/reviews",
        json=valid_review_payload(),
    )
    assert created.status_code == 200

    response = client.get(
        "/api/v1/reviews",
        params={"status": "COMPLETED"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "COMPLETED"


@pytest.mark.parametrize(
    "query",
    [
        "skip=-1",
        "limit=0",
        "limit=101",
        "status=NOT_A_STATUS",
    ],
)
def test_integration_list_reviews_rejects_invalid_query_parameters(
    api_environment,
    query,
):
    client, _, _ = api_environment
    response = client.get(f"/api/v1/reviews?{query}")
    assert response.status_code == 422


def test_integration_list_reviews_rejects_invalid_date_range(api_environment):
    client, _, _ = api_environment

    response = client.get(
        "/api/v1/reviews",
        params={
            "start_date": "2026-11-14T12:00:00",
            "end_date": "2026-11-13T12:00:00",
        },
    )

    assert response.status_code == 422


def make_final_findings() -> list[Finding]:
    """Create varied consolidated findings for Findings API filter tests."""

    base = make_finding()
    variants = [
        {
            "agent_name": "SecurityAgent",
            "title": "High severity security issue",
            "severity": Severity.HIGH,
            "file_path": "app/main.py",
            "start_line": 10,
            "end_line": 10,
        },
        {
            "agent_name": "DiffAgent",
            "title": "Critical authentication issue",
            "severity": Severity.CRITICAL,
            "file_path": "app/auth.py",
            "start_line": 20,
            "end_line": 22,
        },
        {
            "agent_name": "StyleAgent",
            "title": "Medium style issue",
            "severity": Severity.MEDIUM,
            "file_path": "app/main.py",
            "start_line": 30,
            "end_line": 30,
        },
        {
            "agent_name": "TestCoverageAgent",
            "title": "Low test coverage issue",
            "severity": Severity.LOW,
            "file_path": "tests/test_main.py",
            "start_line": 5,
            "end_line": 5,
        },
        {
            "agent_name": "SecurityAgent",
            "title": "Informational security note",
            "severity": Severity.INFO,
            "file_path": "app/config.py",
            "start_line": 8,
            "end_line": 8,
        },
    ]

    return [
        base.model_copy(
            update={
                **variant,
                "finding_id": str(uuid4()),
            }
        )
        for variant in variants
    ]


def create_review_with_final_findings(client, pipeline_holder):
    """Create a persisted review and replace its final findings with test data."""

    response = client.post(
        "/api/v1/reviews",
        json=valid_review_payload(),
    )
    assert response.status_code == 200
    review_id = response.json()["review_id"]

    pipeline = pipeline_holder["pipeline"]
    asyncio.run(
        pipeline.state_manager.update_final_review(
            review_id=review_id,
            findings=make_final_findings(),
            recommendation=MergeRecommendation.REQUEST_CHANGES,
            summary="Test review with varied consolidated findings.",
        )
    )
    return review_id


def test_integration_list_findings_returns_paginated_results(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={"skip": 0, "limit": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5
    assert data["skip"] == 0
    assert data["limit"] == 2
    assert data["has_more"] is True


def test_integration_list_findings_second_page_has_no_more_results(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={"skip": 4, "limit": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["total"] == 5
    assert data["has_more"] is False


def test_integration_list_findings_filters_by_severity(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={"severity": "CRITICAL"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["severity"] == "CRITICAL"
    assert data["items"][0]["title"] == "Critical authentication issue"


def test_integration_list_findings_filters_by_agent_name(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={"agent_name": "SecurityAgent"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert all(item["agent_name"] == "SecurityAgent" for item in data["items"])


def test_integration_list_findings_filters_by_file_path(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={"file_path": "app/main.py"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert all(item["file_path"] == "app/main.py" for item in data["items"])


def test_integration_list_findings_filters_blocking_only(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={"blocking_only": "true"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert all(item["severity"] in {"CRITICAL", "HIGH"} for item in data["items"])


def test_integration_list_findings_combines_filters(api_environment):
    client, pipeline_holder, _ = api_environment
    review_id = create_review_with_final_findings(client, pipeline_holder)

    response = client.get(
        f"/api/v1/reviews/{review_id}/findings",
        params={
            "severity": "HIGH",
            "agent_name": "SecurityAgent",
            "file_path": "app/main.py",
            "blocking_only": "true",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "High severity security issue"


def test_integration_list_findings_returns_404_for_missing_review(api_environment):
    client, _, _ = api_environment

    response = client.get(
        "/api/v1/reviews/00000000-0000-0000-0000-000000000000/findings"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Review not found."


@pytest.mark.parametrize(
    "query",
    [
        "skip=-1",
        "limit=0",
        "limit=101",
        "severity=NOT_A_SEVERITY",
        "agent_name=",
        "file_path=",
        "blocking_only=not-a-boolean",
    ],
)
def test_integration_list_findings_rejects_invalid_query_parameters(
    api_environment,
    query,
):
    client, _, _ = api_environment
    response = client.get(
        f"/api/v1/reviews/00000000-0000-0000-0000-000000000000/findings?{query}"
    )
    assert response.status_code == 422


def test_integration_unicode_survives_database_persistence(api_environment):
    client, pipeline_holder, _ = api_environment

    # Create a review through the API.
    payload = valid_review_payload()
    payload["repository"] = "org/café-日本-नमस्ते"

    create_response = client.post(
        "/api/v1/reviews",
        json=payload,
    )

    assert create_response.status_code == 200

    review_id = create_response.json()["review_id"]

    # Build Unicode finding data.
    unicode_finding = Finding(
        agent_name="SecurityAgent",
        title="Hardcoded credentials — sécurité",
        description="User-controlled input: café, naïve, नमस्ते, 中文.",
        severity=Severity.HIGH,
        confidence=0.95,
        file_path="app/main.py",
        start_line=10,
        end_line=10,
        evidence="Unicode evidence: résumé — 日本語.",
        recommendation="Validate user input — सुरक्षित रखें.",
        rule_id="UNICODE-TEST-001",
    )

    # Persist the Unicode finding using the existing database-backed manager.
    asyncio.run(
        pipeline_holder["pipeline"].state_manager.update_final_review(
            review_id=review_id,
            findings=[unicode_finding],
            recommendation=MergeRecommendation.REQUEST_CHANGES,
            summary="Unicode persistence test — café, नमस्ते, 中文.",
        )
    )

    # Retrieve the review through the API.
    get_response = client.get(f"/api/v1/reviews/{review_id}")

    assert get_response.status_code == 200

    retrieved = get_response.json()

    assert retrieved["repository"] == "org/café-日本-नमस्ते"
    assert retrieved["summary"] == ("Unicode persistence test — café, नमस्ते, 中文.")

    finding = retrieved["findings"][0]

    assert finding["title"] == "Hardcoded credentials — sécurité"
    assert finding["description"] == ("User-controlled input: café, naïve, नमस्ते, 中文.")
    assert finding["evidence"] == "Unicode evidence: résumé — 日本語."
    assert finding["recommendation"] == ("Validate user input — सुरक्षित रखें.")
