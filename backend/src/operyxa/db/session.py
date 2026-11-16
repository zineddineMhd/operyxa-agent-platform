from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from operyxa.db.models import Base


class Database:
    def __init__(self, url: str):
        kwargs = {} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_all(self) -> None:
        """Tests and throwaway local databases only. Real schemas go through Alembic."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
