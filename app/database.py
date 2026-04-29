from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"timeout": 5},
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Run migrations/init.sql to create extensions and tables (idempotent).

    Executes init.sql directly so Railway (and any non-Docker deployment) gets
    the pgvector extension and schema on first boot — no docker-entrypoint needed.
    """
    import logging
    import os

    logger = logging.getLogger(__name__)
    sql_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "migrations", "init.sql")
    )
    if not os.path.exists(sql_path):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    with open(sql_path) as f:
        sql = f.read()

    async with engine.begin() as conn:
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    await conn.execute(text(stmt))
                except Exception as exc:
                    # Most "errors" here are benign (IF NOT EXISTS) — log and continue
                    logger.debug("Migration statement skipped: %s", exc)


async def is_database_available() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
