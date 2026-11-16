"""Worker entry point: python -m operyxa.worker"""

import asyncio
import logging
import signal
import socket
import uuid

from operyxa.agent.store import RunStore
from operyxa.bootstrap import build_providers, open_checkpointer
from operyxa.config import get_settings
from operyxa.db.session import Database
from operyxa.gateway.gateway import ToolGateway
from operyxa.llm.factory import build_llm
from operyxa.worker.runner import WorkerDeps, worker_loop


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    db = Database(settings.database_url)
    store = RunStore(db)
    gateway = ToolGateway(settings, store, build_providers(settings))
    await gateway.discover()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    async with open_checkpointer(settings) as checkpointer:
        deps = WorkerDeps(settings, db, store, gateway, build_llm(settings), checkpointer)
        logging.getLogger(__name__).info("worker %s ready (llm=%s)", worker_id, deps.llm.model_name)
        await worker_loop(deps, stop, worker_id)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
