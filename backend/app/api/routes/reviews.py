import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_review_pipeline
from app.api.schemas import (
    FindingListResponse,
    ReviewCreateRequest,
    ReviewListResponse,
)
from app.orchestration.review_pipeline import ReviewPipeline
from app.schemas.finding import Severity
from app.schemas.review import Review, ReviewStatus

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/reviews",
    tags=["Reviews"],
)


def _review_from_state(state) -> Review:
    """Convert persisted ChangeState data into the public Review schema."""

    return Review(
        review_id=state.review_id,
        repository=state.repository,
        pull_request_number=state.pull_request_number,
        base_commit=state.base_commit,
        head_commit=state.head_commit,
        status=state.status,
        agent_results=state.agent_results,
        findings=state.final_findings,
        summary=state.change_summary,
        recommendation=state.recommendation,
        created_at=state.created_at,
        completed_at=state.completed_at,
    )


@router.post(
    "",
    response_model=Review,
    status_code=status.HTTP_200_OK,
    summary="Create a code review",
)
async def create_review(
    payload: ReviewCreateRequest,
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> Review:
    """Run the complete multi-agent code review pipeline."""

    try:
        return await pipeline.run(**payload.model_dump())

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception("Code review request failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The code review could not be completed.",
        ) from exc


@router.get(
    "",
    response_model=ReviewListResponse,
    summary="List reviews",
)
async def list_reviews(
    skip: int = Query(default=0, ge=0, description="Number of reviews to skip"),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum reviews to return",
    ),
    repository: str | None = Query(default=None, min_length=1),
    review_status: ReviewStatus | None = Query(default=None, alias="status"),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> ReviewListResponse:
    """List reviews with pagination and optional filters."""

    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_date must be earlier than or equal to end_date.",
        )

    try:
        states, total = await pipeline.state_manager.list_states(
            skip=skip,
            limit=limit,
            repository=repository,
            status=review_status,
            start_date=start_date,
            end_date=end_date,
        )

        items = [_review_from_state(state) for state in states]

        return ReviewListResponse(
            items=items,
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + len(items)) < total,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Could not list code reviews")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Reviews could not be retrieved.",
        ) from exc


@router.get(
    "/{review_id}/findings",
    response_model=FindingListResponse,
    summary="List consolidated findings for a review",
)
async def list_review_findings(
    review_id: str,
    skip: int = Query(default=0, ge=0, description="Number of findings to skip"),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum findings to return",
    ),
    severity: Severity | None = Query(default=None),
    agent_name: str | None = Query(default=None, min_length=1),
    file_path: str | None = Query(default=None, min_length=1),
    blocking_only: bool = Query(default=False),
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> FindingListResponse:
    """List final consolidated findings with optional filters."""

    try:
        findings, total = await pipeline.state_manager.list_findings(
            review_id=review_id,
            skip=skip,
            limit=limit,
            severity=severity,
            agent_name=agent_name,
            file_path=file_path,
            blocking_only=blocking_only,
        )

        return FindingListResponse(
            items=findings,
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + len(findings)) < total,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found.",
        ) from exc

    except Exception as exc:
        logger.exception("Could not list findings for review %s", review_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Findings could not be retrieved.",
        ) from exc


@router.get(
    "/{review_id}",
    response_model=Review,
    summary="Get review results",
)
async def get_review(
    review_id: str,
    pipeline: ReviewPipeline = Depends(get_review_pipeline),
) -> Review:
    """Retrieve a previously created review."""

    try:
        state = await pipeline.state_manager.get_state(review_id)
        return _review_from_state(state)

    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found.",
        ) from exc
