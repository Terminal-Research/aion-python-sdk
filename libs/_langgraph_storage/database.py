import asyncio
import os
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, NotRequired, TypedDict
from uuid import UUID

import structlog
from langgraph.checkpoint.memory import PersistentDict

from langgraph_api import config as langgraph_config
from langgraph_api.utils import AsyncConnectionProto
from langgraph_storage import store
from langgraph_storage.inmem_stream import start_stream, stop_stream

logger = structlog.stdlib.get_logger(__name__)


class Assistant(TypedDict):
    assistant_id: UUID
    graph_id: str
    name: str
    created_at: NotRequired[datetime]
    updated_at: NotRequired[datetime]
    config: dict[str, Any]
    metadata: dict[str, Any]


class Thread(TypedDict):
    thread_id: UUID
    created_at: NotRequired[datetime]
    updated_at: NotRequired[datetime]
    metadata: dict[str, Any]
    status: str


class Run(TypedDict):
    run_id: UUID
    thread_id: UUID
    assistant_id: UUID
    created_at: NotRequired[datetime]
    updated_at: NotRequired[datetime]
    metadata: dict[str, Any]
    status: str


class RunEvent(TypedDict):
    event_id: UUID
    run_id: UUID
    received_at: NotRequired[datetime]
    span_id: UUID
    event: str
    name: str
    tags: list[Any]
    data: dict[str, Any]
    metadata: dict[str, Any]


class AssistantVersion(TypedDict):
    assistant_id: UUID
    version: int
    graph_id: str
    config: dict[str, Any]
    metadata: dict[str, Any]
    created_at: NotRequired[datetime]
    name: str


class GlobalStore(PersistentDict):
    def __init__(self, *args: Any, filename: str, **kwargs: Any) -> None:
        super().__init__(*args, filename=filename, **kwargs)
        self.clear()

    def clear(self):
        assistants = self.get("assistants", [])
        super().clear()
        self["runs"] = []
        self["threads"] = []
        self["assistants"] = [
            a for a in assistants if a["metadata"].get("created_by") == "system"
        ]
        self["assistant_versions"] = []


OPS_FILENAME = os.path.join(".langgraph_api", ".langgraph_ops.pckl")
RETRY_COUNTER_FILENAME = os.path.join(".langgraph_api", ".langgraph_retry_counter.pckl")


class InMemoryRetryCounter:
    def __init__(self):
        self._counters: dict[uuid.UUID, int] = PersistentDict(
            int, filename=RETRY_COUNTER_FILENAME
        )
        self._locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def increment(self, run_id: uuid.UUID) -> int:
        async with self._locks[run_id]:
            self._counters[run_id] += 1
            return self._counters[run_id]

    def close(self):
        self._counters.close()


# Global retry counter for in-memory implementation
GLOBAL_RETRY_COUNTER = InMemoryRetryCounter()
GLOBAL_STORE = GlobalStore(filename=OPS_FILENAME)


class InMemConnectionProto:
    def __init__(self):
        self.filename = OPS_FILENAME
        self.store = GLOBAL_STORE
        self.retry_counter = GLOBAL_RETRY_COUNTER
        self.can_execute = False

    @asynccontextmanager
    async def pipeline(self):
        yield None

    async def execute(self, query: str, *args, **kwargs):
        return None

    def clear(self):
        self.store.clear()
        keys = list(self.retry_counter._counters)
        for key in keys:
            del self.retry_counter._counters[key]
        keys = list(self.retry_counter._locks)
        for key in keys:
            del self.retry_counter._locks[key]
        if os.path.exists(self.filename):
            os.remove(self.filename)


@asynccontextmanager
async def connect(*, __test__: bool = False) -> AsyncIterator[AsyncConnectionProto]:
    yield InMemConnectionProto()


async def start_pool() -> None:
    if store._STORE_CONFIG is None:
        if langgraph_config.STORE_CONFIG:
            config_ = langgraph_config.STORE_CONFIG
            store.set_store_config(config_)

    if not os.path.exists(".langgraph_api"):
        os.mkdir(".langgraph_api")
    if os.path.exists(OPS_FILENAME):
        try:
            GLOBAL_STORE.load()
        except ModuleNotFoundError:
            logger.error(
                "Unable to load cached data - your code has changed in a way that's incompatible with the cache."
                "\nThis usually happens when you've:"
                "\n  - Renamed or moved classes"
                "\n  - Changed class structures"
                "\n  - Pulled updates that modified class definitions in a way that's incompatible with the cache"
                "\n\nRemoving invalid cache data stored at path: .langgraph_api"
            )
            os.remove(OPS_FILENAME)
            os.remove(RETRY_COUNTER_FILENAME)
        except Exception as e:
            logger.error("Failed to load cached data: %s", str(e))
            os.remove(OPS_FILENAME)
            os.remove(RETRY_COUNTER_FILENAME)
    for k in ["runs", "threads", "assistants", "assistant_versions"]:
        if not GLOBAL_STORE.get(k):
            GLOBAL_STORE[k] = []
    for k in ["crons"]:
        if not GLOBAL_STORE.get(k):
            GLOBAL_STORE[k] = {}
    await start_stream()


async def stop_pool() -> None:
    GLOBAL_STORE.close()
    GLOBAL_RETRY_COUNTER.close()
    from langgraph_storage.checkpoint import Checkpointer
    from langgraph_storage.store import STORE

    STORE.close()

    async with Checkpointer():
        pass
    await stop_stream()


async def healthcheck() -> None:
    # What could possibly go wrong?
    pass


def pool_stats() -> dict[str, dict[str, int]]:
    # TODO??
    return {}
