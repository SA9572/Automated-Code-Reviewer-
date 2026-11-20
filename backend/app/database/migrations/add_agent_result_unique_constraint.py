from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

CONSTRAINT_INDEX = "uq_agent_results_review_agent"


async def migrate_agent_result_unique_constraint(
    connection: AsyncConnection,
) -> None:
    """Add database-level uniqueness for each agent per review."""

    duplicates = await connection.execute(
        text(
            """
            SELECT review_id, agent_name, COUNT(*) AS total
            FROM agent_results
            GROUP BY review_id, agent_name
            HAVING COUNT(*) > 1
            """
        )
    )

    if duplicates.first() is not None:
        raise RuntimeError("Migration aborted: duplicate agent results exist.")

    await connection.execute(
        text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS
            {CONSTRAINT_INDEX}
            ON agent_results (review_id, agent_name)
            """
        )
    )
