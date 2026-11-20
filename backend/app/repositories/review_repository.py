from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    AgentResultModel,
    FindingModel,
    FixSuggestionModel,
    ReviewModel,
)
from app.schemas.agent_result import AgentResult, AgentStatus
from app.schemas.finding import Finding
from app.schemas.fix_suggestion import FixSuggestion
from app.schemas.review import (
    MergeRecommendation,
    Review,
    ReviewStatus,
)
from app.state.change_state import ChangeState


def enum_value(value):
    """Return the string value of an Enum or the original value."""
    return value.value if hasattr(value, "value") else value


class ReviewRepository:
    """Database operations for code reviews."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------------------------------------------------
    # CHANGE STATE OPERATIONS
    # Used by StateManager during an active review.
    # ---------------------------------------------------------

    async def create_state(self, state: ChangeState) -> ChangeState:
        """Persist the initial state of a new review."""

        existing = await self.get_by_id(state.review_id)

        if existing is not None:
            raise ValueError(f"Review already exists: {state.review_id}")

        review_record = ReviewModel(
            review_id=state.review_id,
            repository=state.repository,
            pull_request_number=state.pull_request_number,
            base_commit=state.base_commit,
            head_commit=state.head_commit,
            changed_files=list(state.changed_files),
            diff_content=state.diff_content,
            status=enum_value(state.status),
            summary=state.change_summary,
            recommendation=enum_value(state.recommendation),
            created_at=state.created_at,
            updated_at=state.updated_at,
            completed_at=state.completed_at,
        )

        self.session.add(review_record)
        await self.session.commit()

        return await self.get_state(state.review_id)

    async def get_state(self, review_id: str) -> ChangeState:
        """Retrieve a persisted review and reconstruct its ChangeState."""

        statement = (
            select(ReviewModel)
            .where(ReviewModel.review_id == review_id)
            .options(
                selectinload(ReviewModel.agent_results).selectinload(
                    AgentResultModel.findings
                ),
                selectinload(ReviewModel.findings),
            )
        )

        result = await self.session.execute(statement)
        record = result.scalar_one_or_none()

        if record is None:
            raise KeyError(f"Review not found: {review_id}")

        agent_results: dict[str, AgentResult] = {}

        for agent_record in record.agent_results:
            agent_findings = [
                self._finding_from_model(item) for item in agent_record.findings
            ]

            agent_results[agent_record.agent_name] = AgentResult(
                agent_name=agent_record.agent_name,
                status=AgentStatus(agent_record.status),
                summary=agent_record.summary,
                findings=agent_findings,
                execution_time_ms=agent_record.execution_time_ms,
                error=agent_record.error,
            )

        final_findings = [
            self._finding_from_model(item) for item in record.findings if item.is_final
        ]

        return ChangeState(
            review_id=record.review_id,
            repository=record.repository,
            pull_request_number=record.pull_request_number,
            base_commit=record.base_commit,
            head_commit=record.head_commit,
            changed_files=record.changed_files or [],
            diff_content=record.diff_content or "",
            change_summary=record.summary or "",
            agent_results=agent_results,
            final_findings=final_findings,
            recommendation=(
                MergeRecommendation(record.recommendation)
                if record.recommendation
                else MergeRecommendation.REVIEW_REQUIRED
            ),
            status=ReviewStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
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
        Retrieve paginated review states with optional filters.

        Returns:
            A tuple containing the matching states and total count.
        """

        filters = []

        if repository is not None:
            filters.append(ReviewModel.repository == repository)

        if status is not None:
            filters.append(ReviewModel.status == enum_value(status))

        if start_date is not None:
            filters.append(ReviewModel.created_at >= start_date)

        if end_date is not None:
            filters.append(ReviewModel.created_at <= end_date)

        count_statement = select(func.count()).select_from(ReviewModel).where(*filters)
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar_one()

        statement = (
            select(ReviewModel)
            .where(*filters)
            .options(
                selectinload(ReviewModel.agent_results).selectinload(
                    AgentResultModel.findings
                ),
                selectinload(ReviewModel.findings),
            )
            .order_by(
                ReviewModel.created_at.desc(),
                ReviewModel.review_id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(statement)
        records = result.scalars().all()

        states: list[ChangeState] = []

        for record in records:
            agent_results: dict[str, AgentResult] = {}

            for agent_record in record.agent_results:
                agent_findings = [
                    self._finding_from_model(item) for item in agent_record.findings
                ]

                agent_results[agent_record.agent_name] = AgentResult(
                    agent_name=agent_record.agent_name,
                    status=AgentStatus(agent_record.status),
                    summary=agent_record.summary,
                    findings=agent_findings,
                    execution_time_ms=agent_record.execution_time_ms,
                    error=agent_record.error,
                )

            final_findings = [
                self._finding_from_model(item)
                for item in record.findings
                if item.is_final
            ]

            states.append(
                ChangeState(
                    review_id=record.review_id,
                    repository=record.repository,
                    pull_request_number=record.pull_request_number,
                    base_commit=record.base_commit,
                    head_commit=record.head_commit,
                    changed_files=record.changed_files or [],
                    diff_content=record.diff_content or "",
                    change_summary=record.summary or "",
                    agent_results=agent_results,
                    final_findings=final_findings,
                    recommendation=(
                        MergeRecommendation(record.recommendation)
                        if record.recommendation
                        else MergeRecommendation.REVIEW_REQUIRED
                    ),
                    status=ReviewStatus(record.status),
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    completed_at=record.completed_at,
                )
            )

        return states, total

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
        """
        Retrieve paginated consolidated findings for one review.

        Only final findings are returned, matching the public
        Review.findings collection. Agent-level findings remain
        available in agent_results.
        """

        review_exists = await self.session.execute(
            select(ReviewModel.review_id).where(ReviewModel.review_id == review_id)
        )

        if review_exists.scalar_one_or_none() is None:
            raise KeyError(f"Review not found: {review_id}")

        filters = [
            FindingModel.review_id == review_id,
            FindingModel.is_final.is_(True),
        ]

        if severity is not None:
            filters.append(FindingModel.severity == enum_value(severity))

        if agent_name is not None:
            filters.append(FindingModel.agent_name == agent_name)

        if file_path is not None:
            filters.append(FindingModel.file_path == file_path)

        if blocking_only:
            filters.append(FindingModel.is_blocking.is_(True))

        count_statement = select(func.count()).select_from(FindingModel).where(*filters)
        count_result = await self.session.execute(count_statement)
        total = count_result.scalar_one()

        statement = (
            select(FindingModel)
            .where(*filters)
            .order_by(
                FindingModel.file_path.asc(),
                FindingModel.start_line.asc(),
                FindingModel.finding_id.asc(),
            )
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(statement)
        records = result.scalars().all()

        return [self._finding_from_model(item) for item in records], total

    async def update_agent_result(
        self,
        review_id: str,
        result: AgentResult,
    ) -> ChangeState:
        """
        Persist an agent result safely.

        SQLite BEGIN IMMEDIATE serializes independent writers before
        they read or modify the agent result records.
        """

        if self.session.get_bind().dialect.name == "sqlite":
            await self.session.execute(text("BEGIN IMMEDIATE"))

        try:
            review = await self._get_record_for_update(review_id)

            existing = next(
                (
                    item
                    for item in review.agent_results
                    if item.agent_name == result.agent_name
                ),
                None,
            )

            if existing is not None:
                agent_record = existing
                agent_record.status = enum_value(result.status)
                agent_record.summary = result.summary
                agent_record.execution_time_ms = result.execution_time_ms
                agent_record.error = result.error

                # Replace the previous findings for this agent.
                agent_record.findings.clear()

            else:
                agent_record = AgentResultModel(
                    id=str(uuid4()),
                    review_id=review_id,
                    agent_name=result.agent_name,
                    status=enum_value(result.status),
                    summary=result.summary,
                    execution_time_ms=result.execution_time_ms,
                    error=result.error,
                )

                review.agent_results.append(agent_record)

            for finding in result.findings:
                agent_record.findings.append(
                    self._build_finding(
                        finding=finding,
                        review_id=review_id,
                        is_final=False,
                    )
                )

            review.status = ReviewStatus.RUNNING.value
            review.updated_at = datetime.now(UTC)

            await self.session.commit()

            return await self.get_state(review_id)

        except Exception:
            await self.session.rollback()
            raise

    async def update_final_review(
        self,
        review_id: str,
        findings: list[Finding],
        recommendation: MergeRecommendation,
        summary: str,
    ) -> ChangeState:
        """Persist the consolidated findings and final recommendation."""

        review = await self._get_record_for_update(review_id)

        # Remove previous consolidated findings before replacing them.
        review.findings = [item for item in review.findings if not item.is_final]

        for finding in findings:
            review.findings.append(
                self._build_finding(
                    finding=finding,
                    review_id=review_id,
                    is_final=True,
                )
            )

        now = datetime.now(UTC)

        review.summary = summary
        review.recommendation = enum_value(recommendation)
        review.status = ReviewStatus.COMPLETED.value
        review.updated_at = now
        review.completed_at = now

        await self.session.commit()

        return await self.get_state(review_id)

    async def update_state_status(
        self,
        review_id: str,
        status: ReviewStatus,
    ) -> ChangeState:
        """Persist a review status change."""

        review = await self._get_record_for_update(review_id)

        now = datetime.now(UTC)

        review.status = enum_value(status)
        review.updated_at = now

        if status in {
            ReviewStatus.COMPLETED,
            ReviewStatus.FAILED,
            ReviewStatus.PARTIAL,
        }:
            review.completed_at = now

        await self.session.commit()

        return await self.get_state(review_id)

    async def _get_record_for_update(self, review_id: str) -> ReviewModel:
        """Load a review and its relationships for modification."""

        statement = (
            select(ReviewModel)
            .where(ReviewModel.review_id == review_id)
            .options(
                selectinload(ReviewModel.agent_results).selectinload(
                    AgentResultModel.findings
                ),
                selectinload(ReviewModel.findings),
            )
        )

        result = await self.session.execute(statement)
        review = result.scalar_one_or_none()

        if review is None:
            raise KeyError(f"Review not found: {review_id}")

        return review

    # ---------------------------------------------------------
    # FIX SUGGESTION OPERATIONS
    # ---------------------------------------------------------

    async def create_fix_suggestions(
        self,
        review_id: str,
        suggestions: list[FixSuggestion],
    ) -> list[FixSuggestion]:
        """Persist proposed fixes linked to consolidated findings."""

        review_exists = await self.session.execute(
            select(ReviewModel.review_id).where(ReviewModel.review_id == review_id)
        )

        if review_exists.scalar_one_or_none() is None:
            raise KeyError(f"Review not found: {review_id}")

        if not suggestions:
            return []

        for suggestion in suggestions:
            if suggestion.review_id != review_id:
                raise ValueError(
                    "Suggestion review_id does not match the target review."
                )

        requested_finding_ids = {suggestion.finding_id for suggestion in suggestions}

        result = await self.session.execute(
            select(FindingModel.source_finding_id).where(
                FindingModel.review_id == review_id,
                FindingModel.is_final.is_(True),
                FindingModel.source_finding_id.in_(requested_finding_ids),
            )
        )

        existing_finding_ids = set(result.scalars().all())

        missing_ids = requested_finding_ids - existing_finding_ids

        if missing_ids:
            raise ValueError(
                "Suggestions reference findings that do not exist "
                f"in this review: {sorted(missing_ids)}"
            )

        records = [
            FixSuggestionModel(
                suggestion_id=str(suggestion.suggestion_id),
                review_id=review_id,
                finding_id=suggestion.finding_id,
                file_path=suggestion.file_path,
                title=suggestion.title,
                explanation=suggestion.explanation,
                unified_diff=suggestion.unified_diff,
                validation_status=enum_value(suggestion.validation_status),
                approval_status=enum_value(suggestion.approval_status),
                created_at=suggestion.created_at,
                reviewed_at=suggestion.reviewed_at,
            )
            for suggestion in suggestions
        ]

        self.session.add_all(records)
        await self.session.commit()

        return [self._fix_suggestion_from_model(record) for record in records]

    async def list_fix_suggestions(
        self,
        review_id: str,
    ) -> list[FixSuggestion]:
        """Retrieve all proposed fixes belonging to a review."""

        review_exists = await self.session.execute(
            select(ReviewModel.review_id).where(ReviewModel.review_id == review_id)
        )

        if review_exists.scalar_one_or_none() is None:
            raise KeyError(f"Review not found: {review_id}")

        statement = (
            select(FixSuggestionModel)
            .where(FixSuggestionModel.review_id == review_id)
            .order_by(
                FixSuggestionModel.created_at.asc(),
                FixSuggestionModel.suggestion_id.asc(),
            )
        )

        result = await self.session.execute(statement)
        records = result.scalars().all()

        return [self._fix_suggestion_from_model(record) for record in records]

    @staticmethod
    def _fix_suggestion_from_model(
        record: FixSuggestionModel,
    ) -> FixSuggestion:
        """Convert a database record into a FixSuggestion schema."""

        return FixSuggestion(
            suggestion_id=record.suggestion_id,
            review_id=record.review_id,
            finding_id=record.finding_id,
            file_path=record.file_path,
            title=record.title,
            explanation=record.explanation,
            unified_diff=record.unified_diff,
            validation_status=record.validation_status,
            approval_status=record.approval_status,
            created_at=record.created_at,
            reviewed_at=record.reviewed_at,
        )

    # ---------------------------------------------------------
    # EXISTING COMPLETED REVIEW OPERATIONS
    # Retained for backward compatibility.
    # ---------------------------------------------------------

    async def create(self, review: Review) -> ReviewModel:
        """Persist a completed review, its agent results, and findings."""

        review_record = ReviewModel(
            review_id=str(review.review_id),
            repository=review.repository,
            pull_request_number=review.pull_request_number,
            base_commit=review.base_commit,
            head_commit=review.head_commit,
            status=enum_value(review.status),
            summary=review.summary,
            recommendation=enum_value(review.recommendation),
            created_at=review.created_at,
            completed_at=review.completed_at,
        )

        for agent_result in review.agent_results.values():
            agent_record = AgentResultModel(
                id=str(uuid4()),
                review_id=review_record.review_id,
                agent_name=agent_result.agent_name,
                status=enum_value(agent_result.status),
                summary=agent_result.summary,
                execution_time_ms=agent_result.execution_time_ms,
                error=agent_result.error,
            )

            for finding in agent_result.findings:
                agent_record.findings.append(
                    self._build_finding(
                        finding=finding,
                        review_id=review_record.review_id,
                        is_final=False,
                    )
                )

            review_record.agent_results.append(agent_record)

        for finding in review.findings:
            review_record.findings.append(
                self._build_finding(
                    finding=finding,
                    review_id=review_record.review_id,
                    is_final=True,
                )
            )

        self.session.add(review_record)
        await self.session.commit()

        return await self.get_by_id(review_record.review_id)

    @staticmethod
    def _build_finding(
        finding: Finding,
        review_id: str,
        is_final: bool,
    ) -> FindingModel:
        """Convert a Pydantic Finding into a database record."""

        return FindingModel(
            finding_id=str(uuid4()),
            source_finding_id=finding.finding_id,
            review_id=review_id,
            agent_name=finding.agent_name,
            title=finding.title,
            description=finding.description,
            severity=enum_value(finding.severity),
            confidence=finding.confidence,
            file_path=finding.file_path,
            start_line=finding.start_line,
            end_line=finding.end_line,
            evidence=finding.evidence,
            recommendation=finding.recommendation,
            rule_id=finding.rule_id,
            is_blocking=finding.is_blocking,
            is_final=is_final,
        )

    @staticmethod
    def _finding_from_model(record: FindingModel) -> Finding:
        """Convert a database finding back into a Pydantic Finding."""

        return Finding(
            finding_id=record.source_finding_id,
            agent_name=record.agent_name,
            title=record.title,
            description=record.description,
            severity=record.severity,
            confidence=record.confidence,
            file_path=record.file_path,
            start_line=record.start_line,
            end_line=record.end_line,
            evidence=record.evidence,
            recommendation=record.recommendation,
            rule_id=record.rule_id,
        )

    async def get_by_id(self, review_id: str) -> ReviewModel | None:
        """Retrieve a review and all its related records."""

        statement = (
            select(ReviewModel)
            .where(ReviewModel.review_id == review_id)
            .options(
                selectinload(ReviewModel.agent_results).selectinload(
                    AgentResultModel.findings
                ),
                selectinload(ReviewModel.findings),
            )
        )

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        review_id: str,
        status: str,
    ) -> ReviewModel | None:
        """Update the status of an existing review."""

        review = await self.get_by_id(review_id)

        if review is None:
            return None

        review.status = enum_value(status)

        await self.session.commit()
        return await self.get_by_id(review_id)

    async def delete(self, review_id: str) -> bool:
        """Delete a review and its dependent records."""

        review = await self.get_by_id(review_id)

        if review is None:
            return False

        await self.session.delete(review)
        await self.session.commit()

        return True
