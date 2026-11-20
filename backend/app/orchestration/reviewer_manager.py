import logging
import re
from typing import ClassVar

from app.schemas.agent_result import AgentStatus
from app.schemas.finding import Finding, Severity
from app.schemas.review import (
    MergeRecommendation,
    Review,
    ReviewStatus,
)
from app.state.state_manager import StateManager

logger = logging.getLogger(__name__)


class ReviewerManager:
    """
    Consolidates specialist agent results into a final review.

    Responsibilities:
    - Collect findings.
    - Deduplicate overlapping findings.
    - Preserve useful information from duplicate findings.
    - Rank findings by severity and confidence.
    - Generate a summary.
    - Determine merge recommendation.
    - Persist the final review.
    """

    SEVERITY_RANK: ClassVar[dict[Severity, int]] = {
        Severity.CRITICAL: 5,
        Severity.HIGH: 4,
        Severity.MEDIUM: 3,
        Severity.LOW: 2,
        Severity.INFO: 1,
    }

    def __init__(self, state_manager: StateManager):
        if state_manager is None:
            raise ValueError("state_manager is required")

        self.state_manager = state_manager

    async def finalize(self, review_id: str) -> Review:
        """Generate and persist the final consolidated review."""

        state = await self.state_manager.get_state(review_id)
        agent_results = state.agent_results

        if not agent_results:
            raise ValueError("Cannot finalize a review without agent results")

        successful_agents = [
            result
            for result in agent_results.values()
            if result.status == AgentStatus.SUCCESS
        ]

        unsuccessful_agents = [
            result
            for result in agent_results.values()
            if result.status != AgentStatus.SUCCESS
        ]

        # Collect findings from successful agents only.
        all_findings = []

        for result in successful_agents:
            all_findings.extend(result.findings)

        # Deduplicate and preserve useful information.
        findings = self.deduplicate_findings(all_findings)

        # Sort by severity and confidence.
        findings = self.rank_findings(findings)

        recommendation = self._get_recommendation(
            findings,
            has_failures=bool(unsuccessful_agents),
        )

        summary = self._build_summary(
            findings,
            successful_count=len(successful_agents),
            failed_count=len(unsuccessful_agents),
        )

        updated_state = await self.state_manager.update_final_review(
            review_id=review_id,
            findings=findings,
            recommendation=recommendation,
            summary=summary,
        )

        if unsuccessful_agents:
            final_status = ReviewStatus.PARTIAL
        else:
            final_status = ReviewStatus.COMPLETED

        if not successful_agents:
            final_status = ReviewStatus.FAILED

        if final_status != updated_state.status:
            updated_state = await self.state_manager.update_status(
                review_id,
                final_status,
            )

        logger.info(
            "Review %s finalized: %d findings, recommendation=%s",
            review_id,
            len(findings),
            recommendation.value,
        )

        return Review(
            review_id=updated_state.review_id,
            repository=updated_state.repository,
            pull_request_number=updated_state.pull_request_number,
            base_commit=updated_state.base_commit,
            head_commit=updated_state.head_commit,
            status=updated_state.status,
            agent_results=updated_state.agent_results,
            findings=updated_state.final_findings,
            summary=updated_state.change_summary,
            recommendation=updated_state.recommendation,
            created_at=updated_state.created_at,
            completed_at=updated_state.completed_at,
        )

    @classmethod
    def rank_findings(
        cls,
        findings: list[Finding],
    ) -> list[Finding]:
        """Sort findings by severity, then confidence."""

        return sorted(
            findings,
            key=lambda finding: (
                cls.SEVERITY_RANK[finding.severity],
                finding.confidence,
            ),
            reverse=True,
        )

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        """Normalize text for reliable comparisons."""

        if not value:
            return ""

        return re.sub(
            r"\s+",
            " ",
            value.strip().casefold(),
        )

    @classmethod
    def _normalize_path(cls, file_path: str) -> str:
        """Normalize Windows and Unix file paths."""

        normalized = file_path.replace("\\", "/")

        normalized = re.sub(
            r"/+",
            "/",
            normalized,
        )

        return cls._normalize_text(normalized).strip("/")

    @staticmethod
    def _has_line_range(finding: Finding) -> bool:
        return finding.start_line is not None and finding.end_line is not None

    @classmethod
    def _locations_compatible(
        cls,
        first: Finding,
        second: Finding,
    ) -> bool:
        """
        Return False only when both findings have known,
        non-overlapping line ranges.

        Missing line information is treated as unknown,
        not as proof that findings are different.
        """

        if not (cls._has_line_range(first) and cls._has_line_range(second)):
            return True

        return not (
            first.end_line < second.start_line or second.end_line < first.start_line
        )

    @classmethod
    def _same_issue(
        cls,
        first: Finding,
        second: Finding,
    ) -> bool:
        """
        Determine whether two findings represent the same issue.

        Matching evidence can establish duplication across different
        agents even when their rule IDs differ.

        Different rule IDs from the same agent remain separate.
        """

        first_path = cls._normalize_path(first.file_path)
        second_path = cls._normalize_path(second.file_path)

        if first_path != second_path:
            return False

        if not cls._locations_compatible(first, second):
            return False

        first_evidence = cls._normalize_text(first.evidence)
        second_evidence = cls._normalize_text(second.evidence)

        first_rule = cls._normalize_text(first.rule_id)
        second_rule = cls._normalize_text(second.rule_id)

        # Different explicit rules from the same agent are distinct.
        if (
            first_rule
            and second_rule
            and first_rule != second_rule
            and first.agent_name == second.agent_name
        ):
            return False

        # Across different agents, conflicting rule IDs may merge
        # only when the normalized evidence is an exact match.
        if first_rule and second_rule and first_rule != second_rule:
            return bool(
                first.agent_name != second.agent_name
                and first_evidence
                and first_evidence == second_evidence
            )

        # Exact evidence is a strong duplicate signal.
        if first_evidence and second_evidence and first_evidence == second_evidence:
            return True

        # Same rule and overlapping known locations.
        if (
            first_rule
            and first_rule == second_rule
            and cls._has_line_range(first)
            and cls._has_line_range(second)
        ):
            return True

        first_title = cls._normalize_text(first.title)
        second_title = cls._normalize_text(second.title)

        # Same explicit rule and same title.
        if (
            first_rule
            and first_rule == second_rule
            and first_title
            and first_title == second_title
        ):
            return True

        # Without rule IDs, use matching titles only when
        # at least one finding has no evidence.
        return bool(
            not first_rule
            and not second_rule
            and first_title
            and first_title == second_title
            and (not first_evidence or not second_evidence)
        )

    @classmethod
    def _append_unique_text(
        cls,
        existing: str,
        additional: str,
    ) -> str:
        """Append additional text without duplicating existing content."""

        additional = additional.strip()

        if not additional:
            return existing

        existing_parts = [cls._normalize_text(part) for part in existing.split("\n")]

        if cls._normalize_text(additional) in existing_parts:
            return existing

        if not existing.strip():
            return additional

        return f"{existing.rstrip()}\nAdditional context: {additional}"

    @classmethod
    def _merge_findings(
        cls,
        existing: Finding,
        incoming: Finding,
    ) -> Finding:
        """
        Merge two findings while retaining the strongest finding
        and useful information from both agents.
        """

        existing_rank = cls.SEVERITY_RANK[existing.severity]
        incoming_rank = cls.SEVERITY_RANK[incoming.severity]

        # Keep the more severe finding as the primary finding.
        # Use confidence to break severity ties.
        if incoming_rank > existing_rank or (
            incoming_rank == existing_rank and incoming.confidence > existing.confidence
        ):
            primary = incoming.model_copy(deep=True)
            secondary = existing
        else:
            primary = existing.model_copy(deep=True)
            secondary = incoming

        primary.description = cls._append_unique_text(
            primary.description,
            secondary.description,
        )

        primary.evidence = cls._append_unique_text(
            primary.evidence,
            secondary.evidence,
        )

        primary.recommendation = cls._append_unique_text(
            primary.recommendation,
            secondary.recommendation,
        )

        # Merge line ranges while preserving available information.
        available_starts = [
            line
            for line in (existing.start_line, incoming.start_line)
            if line is not None
        ]

        available_ends = [
            line for line in (existing.end_line, incoming.end_line) if line is not None
        ]

        if available_starts:
            primary.start_line = min(available_starts)

        if available_ends:
            primary.end_line = max(available_ends)

        # Preserve a rule ID if the primary finding lacks one.
        if not primary.rule_id and secondary.rule_id:
            primary.rule_id = secondary.rule_id

        return primary

    @classmethod
    def deduplicate_findings(
        cls,
        findings: list[Finding],
    ) -> list[Finding]:
        """
        Deduplicate findings using normalized paths, evidence,
        rule IDs, agent identity, and compatible line locations.

        Preserve severity, confidence, descriptions, evidence,
        recommendations, and line-range information.
        """

        unique: list[Finding] = []

        for finding in findings:
            duplicate_index = None

            for index, existing in enumerate(unique):
                if cls._same_issue(existing, finding):
                    duplicate_index = index
                    break

            if duplicate_index is None:
                unique.append(finding.model_copy(deep=True))
                continue

            unique[duplicate_index] = cls._merge_findings(
                unique[duplicate_index],
                finding,
            )

        return unique

    @staticmethod
    def _get_recommendation(
        findings: list[Finding],
        has_failures: bool = False,
    ) -> MergeRecommendation:
        """
        Determine the merge recommendation.

        CRITICAL/HIGH findings always request changes.
        Failed agents prevent automatic approval.
        MEDIUM findings require manual review.
        """

        for finding in findings:
            if finding.severity in {
                Severity.CRITICAL,
                Severity.HIGH,
            }:
                return MergeRecommendation.REQUEST_CHANGES

        if has_failures:
            return MergeRecommendation.REVIEW_REQUIRED

        for finding in findings:
            if finding.severity == Severity.MEDIUM:
                return MergeRecommendation.REVIEW_REQUIRED

        return MergeRecommendation.APPROVE

    @staticmethod
    def _build_summary(
        findings: list[Finding],
        successful_count: int,
        failed_count: int,
    ) -> str:
        """Build a summary containing finding and agent counts."""

        counts = {severity.value: 0 for severity in Severity}

        for finding in findings:
            counts[finding.severity.value] += 1

        return (
            f"Review completed with {len(findings)} unique findings. "
            f"Agents successful: {successful_count}; "
            f"agents unsuccessful: {failed_count}. "
            f"Critical: {counts['CRITICAL']}, "
            f"High: {counts['HIGH']}, "
            f"Medium: {counts['MEDIUM']}, "
            f"Low: {counts['LOW']}, "
            f"Info: {counts['INFO']}."
        )
