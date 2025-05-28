import asyncio
import os
import threading
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from langgraph.checkpoint.memory import PersistentDict
from langgraph.store.base import BaseStore, Op, Result
from langgraph.store.base.batch import AsyncBatchedBaseStore
from langgraph.store.memory import InMemoryStore

from langgraph_api.graph import resolve_embeddings

_STORE_CONFIG = None


class DiskBackedInMemStore(InMemoryStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._data = PersistentDict(dict, filename=_STORE_FILE)
        self._vectors = PersistentDict(lambda: defaultdict(dict), filename=_VECTOR_FILE)
        self._load_data(self._data, which="data")
        self._load_data(self._vectors, which="vectors")

    def _load_data(self, container: PersistentDict, which: str) -> None:
        if not container.filename:
            return
        try:
            container.load()
        except FileNotFoundError:
            # It's okay if the file doesn't exist yet
            pass

        except (EOFError, ValueError) as e:
            raise RuntimeError(
                f"Failed to load store {which} from {container.filename}. "
                "This may be due to changes in the stored data structure. "
                "Consider clearing the local store by running: rm -rf .langgraph_api"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Unexpected error loading store {which} from {container.filename}: {str(e)}"
            ) from e

    async def start_ttl_sweeper(self) -> asyncio.Task[None]:
        return asyncio.create_task(asyncio.sleep(0))

    def close(self) -> None:
        self._data.close()
        self._vectors.close()


class BatchedStore(AsyncBatchedBaseStore):
    def __init__(self, store: BaseStore) -> None:
        super().__init__()
        self._store = store

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return self._store.batch(ops)

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return await self._store.abatch(ops)

    def close(self) -> None:
        self._store.close()


_STORE_FILE = os.path.join(".langgraph_api", "store.pckl")
_VECTOR_FILE = os.path.join(".langgraph_api", "store.vectors.pckl")
os.makedirs(".langgraph_api", exist_ok=True)
STORE = DiskBackedInMemStore()
BATCHED_STORE = threading.local()


def set_store_config(config: dict) -> None:
    global _STORE_CONFIG, STORE
    _STORE_CONFIG = config.copy()
    _STORE_CONFIG["index"]["embed"] = resolve_embeddings(_STORE_CONFIG.get("index", {}))
    # Re-create the store
    STORE.close()
    STORE = DiskBackedInMemStore(index=_STORE_CONFIG.get("index", {}))


def Store(*args: Any, **kwargs: Any) -> DiskBackedInMemStore:
    if not hasattr(BATCHED_STORE, "store"):
        BATCHED_STORE.store = BatchedStore(STORE)
    return BATCHED_STORE.store
