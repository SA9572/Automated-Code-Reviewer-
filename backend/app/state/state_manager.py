import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.review_repository import ReviewRepository
from app.schemas.agent_result import AgentResult
from app.schemas.finding import Finding
from app.schemas.fix_suggestion import FixSuggestion
from app.schemas.review import MergeRecommendation, ReviewStatus
from app.state.change_state import ChangeState

T = TypeVar("T")

SQLITE_MAX_ATTEMPTS = 4
SQLITE_RETRY_BASE_DELAY_SECONDS = 0.05


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


class StateManager:
    """
    Manages review state.

    Uses in-memory storage when no session factory is provided.
    Uses database persistence when a database session factory is provided.

    Transient SQLite lock errors are retried with bounded exponential
    backoff. Each retry uses a fresh database session.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | None = None,
    ):
        self._states: dict[str, ChangeState] = {}
        self._fix_suggestions: dict[str, list[FixSuggestion]] = {}
        self._lock = asyncio.Lock()
        self._session_factory = session_factory

    async def _run_with_sqlite_retry(
        self,
        operation: Callable[[ReviewRepository], Awaitable[T]],
    ) -> T:
        """
        Run a repository operation with bounded retries for SQLite locks.

        Only SQLite OperationalError messages containing
        'database is locked' or 'database is busy' are retried.
        Every attempt receives a fresh session and transaction.
        """

        if self._session_factory is None:
            raise RuntimeError("Database retry helper requires a session factory.")

        for attempt in range(SQLITE_MAX_ATTEMPTS):
            retry_error: OperationalError | None = None

            async with self._session_factory() as session:
                repository = ReviewRepository(session)

                try:
                    return await operation(repository)

                except OperationalError as exc:
                    await session.rollback()

                    is_sqlite = session.get_bind().dialect.name == "sqlite"
                    error_message = str(exc.orig).lower()

                    is_transient_lock = (
                        "database is locked" in error_message
                        or "database is busy" in error_message
                    )

                    if not is_sqlite or not is_transient_lock:
                        raise

                    retry_error = exc

            # The failed session has now been closed.
            if retry_error is not None:
                if attempt == SQLITE_MAX_ATTEMPTS - 1:
                    raise retry_error

                delay = SQLITE_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                await asyncio.sleep(delay)

        raise RuntimeError("SQLite retry loop exited unexpectedly.")

    async def create_state(self, state: ChangeState) -> ChangeState:
        async with self._lock:
            if self._session_factory is None:
                if state.review_id in self._states:
                    raise ValueError(f"Review already exists: {state.review_id}")

                self._states[state.review_id] = state.model_copy(deep=True)
                return state.model_copy(deep=True)

            return await self._run_with_sqlite_retry(
                lambda repository: repository.create_state(state)
            )

    async def get_state(self, review_id: str) -> ChangeState:
        async with self._lock:
            if self._session_factory is None:
                state = self._states.get(review_id)

                if state is None:
                    raise KeyError(f"Review not found: {review_id}")

                return state.model_copy(deep=True)

            return await self._run_with_sqlite_retry(
                lambda repository: repository.get_state(review_id)
            )

    async def list_states(
        self,
        skip: int = 0,
        limit: int = 10,
        repository: str | None = None,
        status: ReviewStatus | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[ChangeState], int]:
        """
        List review states with optional filters and pagination.

        Returns a page of states and the total number of matching states
        before pagination is applied.
        """

        async with self._lock:
            if self._session_factory is None:
                states = list(self._states.values())

                if repository is not None:
                    states = [
                        state for state in states if state.repository == repository
                    ]

                if status is not None:
                    states = [state for state in states if state.status == status]

                if start_date is not None:
                    states = [
                        state for state in states if state.created_at >= start_date
                    ]

                if end_date is not None:
                    states = [state for state in states if state.created_at <= end_date]

                # Keep ordering consistent with the database repository.
                states.sort(
                    key=lambda state: (
                        state.created_at,
                        state.review_id,
                    ),
                    reverse=True,
                )

                total = len(states)
                page = states[skip : skip + limit]

                return (
                    [state.model_copy(deep=True) for state in page],
                    total,
                )

            return await self._run_with_sqlite_retry(
                lambda repository_obj: repository_obj.list_states(
                    skip=skip,
                    limit=limit,
                    repository=repository,
                    status=status,
                    start_date=start_date,
                    end_date=end_date,
                )
            )

    async def list_findings(
        self,
        review_id: str,
        skip: int = 0,
        limit: int = 10,
        severity: str | None = None,
        agent_name: str | None = None,
        file_path: str | None = None,
        blocking_only: bool = False,
    ) -> tuple[list[Finding], int]:
        """List consolidated findings for a review with filters and pagination."""

        async with self._lock:
            if self._session_factory is None:
                state = self._states.get(review_id)

                if state is None:
                    raise KeyError(f"Review not found: {review_id}")

                findings = list(state.final_findings)

                if severity is not None:
                    expected_severity = _enum_value(severity)
                    findings = [
                        finding
                        for finding in findings
                        if _enum_value(finding.severity) == expected_severity
                    ]

                if agent_name is not None:
                    findings = [
                        finding
                        for finding in findings
                        if finding.agent_name == agent_name
                    ]

                if file_path is not None:
                    findings = [
                        finding
                        for finding in findings
                        if finding.file_path == file_path
                    ]

                if blocking_only:
                    findings = [finding for finding in findings if finding.is_blocking]

                findings.sort(
                    key=lambda finding: (
                        finding.file_path or "",
                        finding.start_line or 0,
                        finding.finding_id,
                    )
                )

                total = len(findings)
                page = findings[skip : skip + limit]

                return (
                    [finding.model_copy(deep=True) for finding in page],
                    total,
                )

            return await self._run_with_sqlite_retry(
                lambda repository: repository.list_findings(
                    review_id=review_id,
                    skip=skip,
                    limit=limit,
                    severity=severity,
                    agent_name=agent_name,
                    file_path=file_path,
                    blocking_only=blocking_only,
                )
            )

    async def update_agent_result(
        self,
        review_id: str,
        result: AgentResult,
    ) -> ChangeState:
        async with self._lock:
            if self._session_factory is None:
                state = self._states.get(review_id)

                if state is None:
                    raise KeyError(f"Review not found: {review_id}")

                state.agent_results[result.agent_name] = result.model_copy(deep=True)
                state.status = ReviewStatus.RUNNING
                state.updated_at = datetime.now(UTC)

                return state.model_copy(deep=True)

            return await self._run_with_sqlite_retry(
                lambda repository: repository.update_agent_result(
                    review_id,
                    result,
                )
            )

    async def update_final_review(
        self,
        review_id: str,
        findings: list[Finding],
        recommendation: MergeRecommendation,
        summary: str,
    ) -> ChangeState:
        async with self._lock:
            if self._session_factory is None:
                state = self._states.get(review_id)

                if state is None:
                    raise KeyError(f"Review not found: {review_id}")

                state.final_findings = [
                    finding.model_copy(deep=True) for finding in findings
                ]
                state.recommendation = recommendation
                state.change_summary = summary
                state.status = ReviewStatus.COMPLETED

                now = datetime.now(UTC)
                state.updated_at = now
                state.completed_at = now

                return state.model_copy(deep=True)

            return await self._run_with_sqlite_retry(
                lambda repository: repository.update_final_review(
                    review_id,
                    findings,
                    recommendation,
                    summary,
                )
            )

    async def update_status(
        self,
        review_id: str,
        status: ReviewStatus,
    ) -> ChangeState:
        async with self._lock:
            if self._session_factory is None:
                state = self._states.get(review_id)

                if state is None:
                    raise KeyError(f"Review not found: {review_id}")

                state.status = status
                state.updated_at = datetime.now(UTC)

                if status in {
                    ReviewStatus.COMPLETED,
                    ReviewStatus.FAILED,
                    ReviewStatus.PARTIAL,
                }:
                    state.completed_at = state.updated_at

                return state.model_copy(deep=True)

            return await self._run_with_sqlite_retry(
                lambda repository: repository.update_state_status(
                    review_id,
                    status,
                )
            )

    async def save_fix_suggestions(
        self,
        review_id: str,
        suggestions: list[FixSuggestion],
    ) -> list[FixSuggestion]:
        """
        Save fix suggestions for a review.

        In-memory mode validates review and finding references.
        Database mode delegates persistence and validation to the repository.
        """

        async with self._lock:
            if self._session_factory is None:
                state = self._states.get(review_id)

                if state is None:
                    raise KeyError(f"Review not found: {review_id}")

                finding_ids = {finding.finding_id for finding in state.final_findings}

                for suggestion in suggestions:
                    if suggestion.review_id != review_id:
                        raise ValueError(
                            "Fix suggestion review_id does not match "
                            f"the requested review: {suggestion.suggestion_id}"
                        )

                    if suggestion.finding_id not in finding_ids:
                        raise ValueError(
                            "Fix suggestion references an unknown finding: "
                            f"{suggestion.finding_id}"
                        )

                stored_suggestions = [
                    suggestion.model_copy(deep=True) for suggestion in suggestions
                ]

                self._fix_suggestions.setdefault(review_id, []).extend(
                    stored_suggestions
                )

                return [
                    suggestion.model_copy(deep=True)
                    for suggestion in stored_suggestions
                ]

            return await self._run_with_sqlite_retry(
                lambda repository: repository.create_fix_suggestions(
                    review_id,
                    suggestions,
                )
            )

    async def get_fix_suggestions(
        self,
        review_id: str,
    ) -> list[FixSuggestion]:
        """Retrieve all fix suggestions associated with a review."""

        async with self._lock:
            if self._session_factory is None:
                if review_id not in self._states:
                    raise KeyError(f"Review not found: {review_id}")

                suggestions = self._fix_suggestions.get(review_id, [])

                return [suggestion.model_copy(deep=True) for suggestion in suggestions]

            return await self._run_with_sqlite_retry(
                lambda repository: repository.list_fix_suggestions(review_id)
            )
