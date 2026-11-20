import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from app.agents.base_agent import BaseAgent
from app.agents.fix_suggestion_agent import FixSuggestionAgent
from app.orchestration.agent_framework_workflow import AgentFrameworkWorkflow
from app.orchestration.parallel_orchestrator import ParallelOrchestrator
from app.orchestration.reviewer_manager import ReviewerManager
from app.schemas.review import Review, ReviewStatus
from app.state.change_state import ChangeState
from app.state.state_manager import StateManager


logger = logging.getLogger(__name__)


class ReviewPipeline:
    """
    Coordinates the complete automated code review workflow.

    Supports two review modes:

    1. pull_request:
       Uses actual unified Git diffs and test diffs.

    2. baseline:
       Uses actual source-file snapshots from a single repository commit.
       No synthetic unified diffs are created.

    Workflow:
    1. Create review state.
    2. Execute specialist agents concurrently using Microsoft Agent Framework.
    3. Persist agent results.
    4. Consolidate findings through ReviewerManager.
    5. Generate fix suggestions for PR reviews when a real diff exists.
    6. Return the final review.
    """

    def __init__(
        self,
        state_manager: StateManager | None = None,
        agents: Sequence[BaseAgent] | None = None,
        max_concurrency: int = 4,
        fix_suggestion_agent: FixSuggestionAgent | None = None,
    ):
        self._owns_agents = agents is None
        self._owns_fix_suggestion_agent = fix_suggestion_agent is None
        self._closed = False
        self._closed_services: set[int] = set()

        self.state_manager = state_manager or StateManager()

        self.orchestrator = ParallelOrchestrator(
            agents=agents,
            state_manager=self.state_manager,
            max_concurrency=max_concurrency,
        )

        self.framework_workflow = AgentFrameworkWorkflow(
            self.orchestrator
        )

        self.reviewer_manager = ReviewerManager(
            state_manager=self.state_manager
        )

        self.fix_suggestion_agent = (
            fix_suggestion_agent
            if fix_suggestion_agent is not None
            else FixSuggestionAgent()
        )

    async def close(self) -> None:
        """Close owned LLM services safely and retry failed closures."""
        if self._closed:
            return

        services: dict[int, Any] = {}

        if self._owns_agents:
            for agent in self.orchestrator.agents:
                service = getattr(agent, "llm_service", None)

                if service is not None:
                    services[id(service)] = service

        if self._owns_fix_suggestion_agent:
            service = getattr(
                self.fix_suggestion_agent,
                "llm_service",
                None,
            )

            if service is not None:
                services[id(service)] = service

        errors: list[Exception] = []

        for service_id, service in services.items():
            if service_id in self._closed_services:
                continue

            close = getattr(service, "close", None)

            if not callable(close):
                self._closed_services.add(service_id)
                continue

            try:
                await close()
                self._closed_services.add(service_id)

            except Exception as exc:
                logger.exception(
                    "Failed to close an owned LLM service: %s",
                    type(exc).__name__,
                )
                errors.append(exc)

        if errors:
            raise RuntimeError(
                f"Failed to close {len(errors)} owned LLM service(s)."
            ) from errors[0]

        self._closed = True

    async def run(
        self,
        repository: str,
        base_commit: str,
        head_commit: str,
        diff_content: str,
        changed_files: list[str] | None = None,
        pull_request_number: int | None = None,
        test_diff: str | None = None,
        test_files: list[str] | None = None,
        review_mode: str = "pull_request",
        source_files: list[dict[str, str]] | None = None,
        test_source_files: list[dict[str, str]] | None = None,
        snapshot_complete: bool = False,
    ) -> Review:
        """
        Run the complete review pipeline.

        Baseline-specific arguments:
            review_mode:
                "baseline" or "pull_request".

            source_files:
                Actual source file snapshots. Each item contains path/content.

            test_source_files:
                Actual recognized test-file snapshots. Each item contains
                path/content.

            snapshot_complete:
                True only when the repository tree was fully enumerated and
                all eligible source blobs were retrieved successfully.
        """
        if not isinstance(diff_content, str):
            raise ValueError("diff_content must be a string")

        if not isinstance(repository, str) or not repository.strip():
            raise ValueError("repository must be a non-empty string")

        if not isinstance(base_commit, str) or not base_commit.strip():
            raise ValueError("base_commit must be a non-empty string")

        if not isinstance(head_commit, str) or not head_commit.strip():
            raise ValueError("head_commit must be a non-empty string")

        if review_mode not in {"pull_request", "baseline"}:
            raise ValueError(
                "review_mode must be 'pull_request' or 'baseline'"
            )

        changed_files = changed_files or []
        test_files = test_files or []
        source_files = source_files or []
        test_source_files = test_source_files or []

        if review_mode == "baseline":
            if pull_request_number is not None:
                raise ValueError(
                    "Baseline reviews cannot have a pull_request_number"
                )

            if not snapshot_complete:
                raise ValueError(
                    "Baseline review requires a complete repository snapshot"
                )

            if not source_files:
                raise ValueError(
                    "Baseline review requires at least one source file"
                )

            self._validate_snapshot_files(
                source_files,
                "source_files",
            )
            self._validate_snapshot_files(
                test_source_files,
                "test_source_files",
            )

            # A baseline review is not a PR diff. Keep this empty so that
            # agents must use source_files and cannot mistake synthetic
            # patch metadata for source code.
            diff_content = ""

        # Step 1: Create initial review state.
        state = ChangeState(
            repository=repository,
            pull_request_number=pull_request_number,
            base_commit=base_commit,
            head_commit=head_commit,
            changed_files=changed_files,
            diff_content=diff_content,
        )

        await self.state_manager.create_state(state)

        review_id = state.review_id

        logger.info(
            "Starting %s review pipeline: review_id=%s repository=%s",
            review_mode,
            review_id,
            repository,
        )

        # Prepare shared context for specialist agents.
        context = {
            "review_id": review_id,
            "repository": repository,
            "pull_request_number": pull_request_number,
            "base_commit": base_commit,
            "head_commit": head_commit,
            "changed_files": changed_files,
            "diff_content": diff_content,
            "test_diff": test_diff,
            "test_files": test_files,
            "review_mode": review_mode,
            "source_files": source_files,
            "test_source_files": test_source_files,
            "snapshot_complete": snapshot_complete,
        }

        try:
            # Step 2 and 3:
            # Execute specialist agents through the Microsoft Agent
            # Framework workflow and persist their results.
            await self.framework_workflow.run(
                context=context,
                review_id=review_id,
            )

            # Step 4: Consolidate and persist the final review.
            final_review = await self.reviewer_manager.finalize(review_id)

            logger.info(
                "Review consolidation completed: review_id=%s findings=%d",
                review_id,
                len(final_review.findings),
            )

            # Step 5: Generate fix suggestions only when a genuine PR diff
            # exists. Baseline source snapshots are not unified diffs and
            # must not be passed to the fix-suggestion patch generator.
            if (
                review_mode == "pull_request"
                and final_review.findings
                and diff_content.strip()
            ):
                try:
                    suggestions = (
                        await self.fix_suggestion_agent.generate_suggestions(
                            review_id=review_id,
                            findings=final_review.findings,
                            diff_content=diff_content,
                        )
                    )

                    if suggestions:
                        await self.state_manager.save_fix_suggestions(
                            review_id=review_id,
                            suggestions=suggestions,
                        )

                    logger.info(
                        "Fix suggestion generation completed: "
                        "review_id=%s suggestions=%d",
                        review_id,
                        len(suggestions),
                    )

                except Exception:
                    logger.exception(
                        "Fix suggestion generation or persistence failed. "
                        "The completed review will still be returned: "
                        "review_id=%s",
                        review_id,
                    )

            else:
                logger.info(
                    "Skipping fix suggestions for review mode %s: "
                    "review_id=%s",
                    review_mode,
                    review_id,
                )

            logger.info(
                "Review pipeline completed: review_id=%s status=%s "
                "findings=%d recommendation=%s",
                review_id,
                final_review.status.value,
                len(final_review.findings),
                final_review.recommendation.value,
            )

            return final_review

        except asyncio.CancelledError:
            logger.warning(
                "Review pipeline cancelled: review_id=%s",
                review_id,
            )

            await self.state_manager.update_status(
                review_id,
                ReviewStatus.FAILED,
            )

            raise

        except Exception:
            logger.exception(
                "Review pipeline failed: review_id=%s",
                review_id,
            )

            await self.state_manager.update_status(
                review_id,
                ReviewStatus.FAILED,
            )

            raise

    @staticmethod
    def _validate_snapshot_files(
        files: list[dict[str, str]],
        field_name: str,
    ) -> None:
        """Validate the structure of source snapshot entries."""
        if not isinstance(files, list):
            raise ValueError(f"{field_name} must be a list")

        for index, item in enumerate(files):
            if not isinstance(item, dict):
                raise ValueError(
                    f"{field_name}[{index}] must be an object"
                )

            path = item.get("path")
            content = item.get("content")

            if not isinstance(path, str) or not path.strip():
                raise ValueError(
                    f"{field_name}[{index}].path must be a non-empty string"
                )

            if not isinstance(content, str):
                raise ValueError(
                    f"{field_name}[{index}].content must be a string"
                )

            normalized = path.replace("\\", "/")
            if (
                normalized.startswith("/")
                or any(part in {"", ".", ".."} for part in normalized.split("/"))
            ):
                raise ValueError(
                    f"{field_name}[{index}].path must be repository-relative"
                )