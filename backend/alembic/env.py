import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from operyxa.config import get_settings
from operyxa.db.models import Base

target_metadata = Base.metadata

# Tables created and migrated by LangGraph's checkpointer, not by Alembic.
CHECKPOINTER_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    table = obj if type_ == "table" else getattr(obj, "table", None)
    table_name = name if type_ == "table" else getattr(table, "name", None)
    return not (reflected and table_name in CHECKPOINTER_TABLES)


def run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(main())
