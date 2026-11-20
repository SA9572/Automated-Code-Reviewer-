from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

REDUNDANT_INDEX = "ix_agent_results_review_id"


async def migrate_remove_redundant_agent_result_index(
    connection: AsyncConnection,
) -> None:
    """Remove the redundant standalone review_id index."""

    await connection.execute(text(f"DROP INDEX IF EXISTS {REDUNDANT_INDEX}"))
