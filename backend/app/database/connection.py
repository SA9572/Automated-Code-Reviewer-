from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Project root: F:\Automated-Code-Review-Board
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Database directory
DATABASE_DIR = PROJECT_ROOT / "data"
DATABASE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATABASE_DIR / "reviews.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}"


# SQLite engine with an explicit lock timeout.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "timeout": 30,
    },
)


# Configure every new SQLite connection.
@event.listens_for(engine.sync_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


# Asynchronous session factory.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Provide an asynchronous database session."""

    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Initialize database tables and migrations."""

    from app.database.base import Base
    from app.database.migrations.add_agent_result_unique_constraint import (
        migrate_agent_result_unique_constraint,
    )
    from app.database.migrations.remove_redundant_agent_result_index import (
        migrate_remove_redundant_agent_result_index,
    )
    from app.database.models import (
        AgentResultModel,
        FindingModel,
        ReviewModel,
    )

    # Ensure all models are registered with SQLAlchemy metadata.
    _ = (ReviewModel, AgentResultModel, FindingModel)

    async with engine.begin() as connection:
        # Create database tables if they do not exist.
        await connection.run_sync(Base.metadata.create_all)

        # Migration 1: Ensure each review has only one result per agent.
        await migrate_agent_result_unique_constraint(connection)

        # Migration 2: Remove the redundant standalone review_id index.
        await migrate_remove_redundant_agent_result_index(connection)


async def close_db() -> None:
    """Dispose of the database engine."""

    await engine.dispose()
