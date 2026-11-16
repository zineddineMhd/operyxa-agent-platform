import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from operyxa.config import get_settings
from operyxa.db.models import Base

target_metadata = Base.metadata


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(main())
