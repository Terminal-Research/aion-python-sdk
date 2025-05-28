"""Implementation of the LangGraph API using in-memory checkpointer & store."""

import asyncio
import base64
import copy
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import structlog
from langgraph.pregel.debug import CheckpointPayload
from langgraph.pregel.types import StateSnapshot
from langgraph_sdk import Auth
from starlette.exceptions import HTTPException

from langgraph_api.asyncio import SimpleTaskGroup, ValueEvent, create_task
from langgraph_api.auth.custom import handle_event
from langgraph_api.command import map_cmd
from langgraph_api.errors import UserInterrupt, UserRollback
from langgraph_api.graph import get_graph
from langgraph_api.schema import (
    Assistant,
    Checkpoint,
    Config,
    Cron,
    IfNotExists,
    MetadataInput,
    MetadataValue,
    MultitaskStrategy,
    OnConflictBehavior,
    QueueStats,
    Run,
    RunStatus,
    StreamMode,
    Thread,
    ThreadStatus,
    ThreadUpdateResponse,
)
from langgraph_api.serde import Fragment
from langgraph_api.utils import fetchone, get_auth_ctx
from langgraph_storage.checkpoint import Checkpointer
from langgraph_storage.database import InMemConnectionProto, connect
from langgraph_storage.inmem_stream import Message, get_stream_manager

logger = structlog.stdlib.get_logger(__name__)


def _ensure_uuid(id_: str | uuid.UUID | None) -> uuid.UUID:
    if isinstance(id_, str):
        return uuid.UUID(id_)
    if id_ is None:
        return uuid4()
    return id_


class WrappedHTTPException(Exception):
    def __init__(self, http_exception: HTTPException):
        self.http_exception = http_exception


# Right now the whole API types as UUID but frequently passes a str
# We ensure UUIDs for eveerything EXCEPT the checkpoint storage/writes,
# which we leave as strings. This is because I'm too lazy to subclass fully
# and we use non-UUID examples in the OSS version


class Authenticated:
    resource: Literal["threads", "crons", "assistants"]

    @classmethod
    def _context(
        cls,
        ctx: Auth.types.BaseAuthContext | None,
        action: Literal["create", "read", "update", "delete", "create_run"],
    ) -> Auth.types.AuthContext | None:
        if not ctx:
            return
        return Auth.types.AuthContext(
            user=ctx.user,
            permissions=ctx.permissions,
            resource=cls.resource,
            action=action,
        )

    @classmethod
    async def handle_event(
        cls,
        ctx: Auth.types.BaseAuthContext | None,
        action: Literal["create", "read", "update", "delete", "search", "create_run"],
        value: Any,
    ) -> Auth.types.FilterType | None:
        ctx = ctx or get_auth_ctx()
        if not ctx:
            return
        return await handle_event(cls._context(ctx, action), value)


class Assistants(Authenticated):
    resource = "assistants"

    @staticmethod
    async def search(
        conn: InMemConnectionProto,
        *,
        graph_id: str | None,
        metadata: MetadataInput,
        limit: int,
        offset: int,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Assistant]:
        metadata = metadata if metadata is not None else {}
        filters = await Assistants.handle_event(
            ctx,
            "search",
            Auth.types.AssistantsSearch(
                graph_id=graph_id, metadata=metadata, limit=limit, offset=offset
            ),
        )

        async def filter_and_yield() -> AsyncIterator[Assistant]:
            assistants = conn.store["assistants"]
            filtered_assistants = [
                assistant
                for assistant in assistants
                if (not graph_id or assistant["graph_id"] == graph_id)
                and (
                    not metadata or is_jsonb_contained(assistant["metadata"], metadata)
                )
                and (not filters or _check_filter_match(assistant["metadata"], filters))
            ]
            filtered_assistants.sort(key=lambda x: x["created_at"], reverse=True)
            for assistant in filtered_assistants[offset : offset + limit]:
                yield assistant

        return filter_and_yield()

    @staticmethod
    async def get(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Assistant]:
        """Get an assistant by ID."""
        assistant_id = _ensure_uuid(assistant_id)
        filters = await Assistants.handle_event(
            ctx,
            "read",
            Auth.types.AssistantsRead(assistant_id=assistant_id),
        )

        async def _yield_result():
            for assistant in conn.store["assistants"]:
                if assistant["assistant_id"] == assistant_id and (
                    not filters or _check_filter_match(assistant["metadata"], filters)
                ):
                    yield assistant

        return _yield_result()

    @staticmethod
    async def put(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        *,
        graph_id: str,
        config: Config,
        metadata: MetadataInput,
        if_exists: OnConflictBehavior,
        name: str,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Assistant]:
        """Insert an assistant."""
        assistant_id = _ensure_uuid(assistant_id)
        metadata = metadata if metadata is not None else {}
        filters = await Assistants.handle_event(
            ctx,
            "create",
            Auth.types.AssistantsCreate(
                assistant_id=assistant_id,
                graph_id=graph_id,
                config=config,
                metadata=metadata,
                name=name,
            ),
        )
        existing_assistant = next(
            (a for a in conn.store["assistants"] if a["assistant_id"] == assistant_id),
            None,
        )
        if existing_assistant:
            if filters and not _check_filter_match(
                existing_assistant["metadata"], filters
            ):
                raise HTTPException(
                    status_code=409, detail=f"Assistant {assistant_id} already exists"
                )
            if if_exists == "raise":
                raise HTTPException(
                    status_code=409, detail=f"Assistant {assistant_id} already exists"
                )
            elif if_exists == "do_nothing":

                async def _yield_existing():
                    yield existing_assistant

                return _yield_existing()

        now = datetime.now(UTC)
        new_assistant: Assistant = {
            "assistant_id": assistant_id,
            "graph_id": graph_id,
            "config": config or {},
            "metadata": metadata or {},
            "name": name,
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        new_version = {
            "assistant_id": assistant_id,
            "version": 1,
            "graph_id": graph_id,
            "config": config or {},
            "metadata": metadata or {},
            "created_at": now,
            "name": name,
        }
        conn.store["assistants"].append(new_assistant)
        conn.store["assistant_versions"].append(new_version)

        async def _yield_new():
            yield new_assistant

        return _yield_new()

    @staticmethod
    async def patch(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        *,
        config: dict | None = None,
        graph_id: str | None = None,
        metadata: MetadataInput | None = None,
        name: str | None = None,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Assistant]:
        """Update an assistant.

        Args:
            assistant_id: The assistant ID.
            graph_id: The graph ID.
            config: The assistant config.
            metadata: The assistant metadata.
            name: The assistant name.

        Returns:
            return the updated assistant model.
        """
        assistant_id = _ensure_uuid(assistant_id)
        metadata = metadata if metadata is not None else {}
        filters = await Assistants.handle_event(
            ctx,
            "update",
            Auth.types.AssistantsUpdate(
                assistant_id=assistant_id,
                graph_id=graph_id,
                config=config,
                metadata=metadata,
                name=name,
            ),
        )
        assistant = next(
            (a for a in conn.store["assistants"] if a["assistant_id"] == assistant_id),
            None,
        )
        if not assistant:
            raise HTTPException(
                status_code=404, detail=f"Assistant {assistant_id} not found"
            )
        elif filters and not _check_filter_match(assistant["metadata"], filters):
            raise HTTPException(
                status_code=404, detail=f"Assistant {assistant_id} not found"
            )

        now = datetime.now(UTC)
        new_version = (
            max(
                v["version"]
                for v in conn.store["assistant_versions"]
                if v["assistant_id"] == assistant_id
            )
            + 1
            if conn.store["assistant_versions"]
            else 1
        )

        # Update assistant_versions table
        if metadata:
            metadata = {
                **assistant["metadata"],
                **metadata,
            }
        new_version_entry = {
            "assistant_id": assistant_id,
            "version": new_version,
            "graph_id": graph_id if graph_id is not None else assistant["graph_id"],
            "config": config if config is not None else assistant["config"],
            "metadata": metadata if metadata is not None else assistant["metadata"],
            "created_at": now,
            "name": name if name is not None else assistant["name"],
        }
        conn.store["assistant_versions"].append(new_version_entry)

        # Update assistants table
        assistant.update(
            {
                "graph_id": new_version_entry["graph_id"],
                "config": new_version_entry["config"],
                "metadata": new_version_entry["metadata"],
                "name": name if name is not None else assistant["name"],
                "updated_at": now,
                "version": new_version,
            }
        )

        async def _yield_updated():
            yield assistant

        return _yield_updated()

    @staticmethod
    async def delete(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[UUID]:
        """Delete an assistant by ID."""
        assistant_id = _ensure_uuid(assistant_id)
        filters = await Assistants.handle_event(
            ctx,
            "delete",
            Auth.types.AssistantsDelete(
                assistant_id=assistant_id,
            ),
        )
        assistant = next(
            (a for a in conn.store["assistants"] if a["assistant_id"] == assistant_id),
            None,
        )

        if not assistant:
            raise HTTPException(
                status_code=404, detail=f"Assistant with ID {assistant_id} not found"
            )
        elif filters and not _check_filter_match(assistant["metadata"], filters):
            raise HTTPException(
                status_code=404, detail=f"Assistant with ID {assistant_id} not found"
            )

        conn.store["assistants"] = [
            a for a in conn.store["assistants"] if a["assistant_id"] != assistant_id
        ]
        # Cascade delete assistant versions, crons, & runs on this assistant
        conn.store["assistant_versions"] = [
            v
            for v in conn.store["assistant_versions"]
            if v["assistant_id"] != assistant_id
        ]
        retained = []
        for run in conn.store["runs"]:
            if run["assistant_id"] == assistant_id:
                res = await Runs.delete(
                    conn, run["run_id"], thread_id=run["thread_id"], ctx=ctx
                )
                await anext(res)
            else:
                retained.append(run)

        async def _yield_deleted():
            yield assistant_id

        return _yield_deleted()

    @staticmethod
    async def set_latest(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        version: int,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Assistant]:
        """Change the version of an assistant."""
        assistant_id = _ensure_uuid(assistant_id)
        filters = await Assistants.handle_event(
            ctx,
            "update",
            Auth.types.AssistantsUpdate(
                assistant_id=assistant_id,
                version=version,
            ),
        )
        assistant = next(
            (a for a in conn.store["assistants"] if a["assistant_id"] == assistant_id),
            None,
        )
        if not assistant:
            raise HTTPException(
                status_code=404, detail=f"Assistant {assistant_id} not found"
            )
        elif filters and not _check_filter_match(assistant["metadata"], filters):
            raise HTTPException(
                status_code=404, detail=f"Assistant {assistant_id} not found"
            )

        version_data = next(
            (
                v
                for v in conn.store["assistant_versions"]
                if v["assistant_id"] == assistant_id and v["version"] == version
            ),
            None,
        )
        if not version_data:
            raise HTTPException(
                status_code=404,
                detail=f"Version {version} not found for assistant {assistant_id}",
            )

        assistant.update(
            {
                "config": version_data["config"],
                "metadata": version_data["metadata"],
                "version": version_data["version"],
                "updated_at": datetime.now(UTC),
            }
        )

        async def _yield_updated():
            yield assistant

        return _yield_updated()

    @staticmethod
    async def get_versions(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        metadata: MetadataInput,
        limit: int,
        offset: int,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Assistant]:
        """Get all versions of an assistant."""
        assistant_id = _ensure_uuid(assistant_id)
        filters = await Assistants.handle_event(
            ctx,
            "read",
            Auth.types.AssistantsRead(assistant_id=assistant_id),
        )
        assistant = next(
            (a for a in conn.store["assistants"] if a["assistant_id"] == assistant_id),
            None,
        )
        if not assistant:
            raise HTTPException(
                status_code=404, detail=f"Assistant {assistant_id} not found"
            )
        versions = [
            v
            for v in conn.store["assistant_versions"]
            if v["assistant_id"] == assistant_id
            and (not metadata or is_jsonb_contained(v["metadata"], metadata))
            and (not filters or _check_filter_match(v["metadata"], filters))
        ]

        # Previously, the name was not included in the assistant_versions table. So we should add them here.
        for v in versions:
            if "name" not in v:
                v["name"] = assistant["name"]

        versions.sort(key=lambda x: x["version"], reverse=True)

        async def _yield_versions():
            for version in versions[offset : offset + limit]:
                yield version

        return _yield_versions()


def is_jsonb_contained(superset: dict[str, Any], subset: dict[str, Any]) -> bool:
    """
    Implements Postgres' @> (containment) operator for dictionaries.
    Returns True if superset contains all key/value pairs from subset.
    """
    for key, value in subset.items():
        if key not in superset:
            return False
        if isinstance(value, dict) and isinstance(superset[key], dict):
            if not is_jsonb_contained(superset[key], value):
                return False
        elif superset[key] != value:
            return False
    return True


def bytes_decoder(obj):
    """Custom JSON decoder that converts base64 back to bytes."""
    if "__type__" in obj and obj["__type__"] == "bytes":
        return base64.b64decode(obj["value"].encode("utf-8"))
    return obj


def _replace_thread_id(data, new_thread_id, thread_id):
    class BytesEncoder(json.JSONEncoder):
        """Custom JSON encoder that handles bytes by converting them to base64."""

        def default(self, obj):
            if isinstance(obj, bytes | bytearray):
                return {
                    "__type__": "bytes",
                    "value": base64.b64encode(
                        obj.replace(
                            str(thread_id).encode(), str(new_thread_id).encode()
                        )
                    ).decode("utf-8"),
                }

            return super().default(obj)

    try:
        json_str = json.dumps(data, cls=BytesEncoder, indent=2)
    except Exception as e:
        raise ValueError(data) from e
    json_str = json_str.replace(str(thread_id), str(new_thread_id))

    # Decoding back from JSON
    d = json.loads(json_str, object_hook=bytes_decoder)
    return d


class Threads(Authenticated):
    resource = "threads"

    @staticmethod
    async def search(
        conn: InMemConnectionProto,
        *,
        metadata: MetadataInput,
        values: MetadataInput,
        status: ThreadStatus | None,
        limit: int,
        offset: int,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Thread]:
        threads = conn.store["threads"]
        filtered_threads: list[Thread] = []
        metadata = metadata if metadata is not None else {}
        values = values if values is not None else {}
        filters = await Threads.handle_event(
            ctx,
            "search",
            Auth.types.ThreadsSearch(
                metadata=metadata,
                values=values,
                status=status,
                limit=limit,
                offset=offset,
            ),
        )

        # Apply filters
        for thread in threads:
            if filters and not _check_filter_match(thread["metadata"], filters):
                continue

            if metadata and not is_jsonb_contained(thread["metadata"], metadata):
                continue

            if (
                values
                and "values" in thread
                and not is_jsonb_contained(thread["values"], values)
            ):
                continue

            if status and thread.get("status") != status:
                continue

            filtered_threads.append(thread)

        # Sort by created_at in descending order
        sorted_threads = sorted(
            filtered_threads, key=lambda x: x["created_at"], reverse=True
        )

        # Apply limit and offset
        paginated_threads = sorted_threads[offset : offset + limit]

        async def thread_iterator() -> AsyncIterator[Thread]:
            for thread in paginated_threads:
                yield thread

        return thread_iterator()

    @staticmethod
    async def _get_with_filters(
        conn: InMemConnectionProto,
        thread_id: UUID,
        filters: Auth.types.FilterType | None,
    ) -> Thread | None:
        thread_id = _ensure_uuid(thread_id)
        matching_thread = next(
            (
                thread
                for thread in conn.store["threads"]
                if thread["thread_id"] == thread_id
            ),
            None,
        )
        if not matching_thread or (
            filters and not _check_filter_match(matching_thread["metadata"], filters)
        ):
            return

        return matching_thread

    @staticmethod
    async def _get(
        conn: InMemConnectionProto,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> Thread | None:
        """Get a thread by ID."""
        thread_id = _ensure_uuid(thread_id)
        filters = await Threads.handle_event(
            ctx,
            "read",
            Auth.types.ThreadsRead(thread_id=thread_id),
        )
        return await Threads._get_with_filters(conn, thread_id, filters)

    @staticmethod
    async def get(
        conn: InMemConnectionProto,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Thread]:
        """Get a thread by ID."""
        matching_thread = await Threads._get(conn, thread_id, ctx)

        if not matching_thread:
            raise HTTPException(
                status_code=404, detail=f"Thread with ID {thread_id} not found"
            )

        async def _yield_result():
            if matching_thread:
                yield matching_thread

        return _yield_result()

    @staticmethod
    async def put(
        conn: InMemConnectionProto,
        thread_id: UUID,
        *,
        metadata: MetadataInput,
        if_exists: OnConflictBehavior,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Thread]:
        """Insert or update a thread."""
        thread_id = _ensure_uuid(thread_id)
        if metadata is None:
            metadata = {}

        # Check if thread already exists
        existing_thread = next(
            (t for t in conn.store["threads"] if t["thread_id"] == thread_id), None
        )
        filters = await Threads.handle_event(
            ctx,
            "create",
            Auth.types.ThreadsCreate(
                thread_id=thread_id, metadata=metadata, if_exists=if_exists
            ),
        )

        if existing_thread:
            if filters and not _check_filter_match(
                existing_thread["metadata"], filters
            ):
                # Should we use a different status code here?
                raise HTTPException(
                    status_code=409, detail=f"Thread with ID {thread_id} already exists"
                )
            if if_exists == "raise":
                raise HTTPException(
                    status_code=409, detail=f"Thread with ID {thread_id} already exists"
                )
            elif if_exists == "do_nothing":

                async def _yield_existing():
                    yield existing_thread

                return _yield_existing()
        # Create new thread
        new_thread: Thread = {
            "thread_id": thread_id,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "metadata": copy.deepcopy(metadata),
            "status": "idle",
            "config": {},
            "values": None,
        }

        # Add to store
        conn.store["threads"].append(new_thread)

        async def _yield_new():
            yield new_thread

        return _yield_new()

    @staticmethod
    async def patch(
        conn: InMemConnectionProto,
        thread_id: UUID,
        *,
        metadata: MetadataValue,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Thread]:
        """Update a thread."""
        thread_list = conn.store["threads"]
        thread_idx = None
        thread_id = _ensure_uuid(thread_id)

        for idx, thread in enumerate(thread_list):
            if thread["thread_id"] == thread_id:
                thread_idx = idx
                break

        if thread_idx is not None:
            filters = await Threads.handle_event(
                ctx,
                "update",
                Auth.types.ThreadsUpdate(thread_id=thread_id, metadata=metadata),
            )
            if not filters or _check_filter_match(
                thread_list[thread_idx]["metadata"], filters
            ):
                thread = copy.deepcopy(thread_list[thread_idx])
                thread["metadata"] = {**thread["metadata"], **metadata}
                thread["updated_at"] = datetime.now(UTC)
                thread_list[thread_idx] = thread

                async def thread_iterator() -> AsyncIterator[Thread]:
                    yield thread

                return thread_iterator()

        async def empty_iterator() -> AsyncIterator[Thread]:
            if False:  # This ensures the iterator is empty
                yield

        return empty_iterator()

    @staticmethod
    async def set_status(
        conn: InMemConnectionProto,
        thread_id: UUID,
        checkpoint: CheckpointPayload | None,
        exception: BaseException | None,
        # This does not accept the auth context since it's only used internally
    ) -> None:
        """Set the status of a thread."""
        thread_id = _ensure_uuid(thread_id)

        async def has_pending_runs(conn_: InMemConnectionProto, tid: UUID) -> bool:
            """Check if thread has any pending runs."""
            return any(
                run["status"] in ("pending", "running") and run["thread_id"] == tid
                for run in conn_.store["runs"]
            )

        # Find the thread
        thread = next(
            (
                thread
                for thread in conn.store["threads"]
                if thread["thread_id"] == thread_id
            ),
            None,
        )

        if not thread:
            raise HTTPException(
                status_code=404, detail=f"Thread {thread_id} not found."
            )

        # Determine has_next from checkpoint
        has_next = False if checkpoint is None else bool(checkpoint["next"])

        # Determine base status
        if exception:
            status = "error"
        elif has_next:
            status = "interrupted"
        else:
            status = "idle"

        # Check for pending runs and update to busy if found
        if await has_pending_runs(conn, thread_id):
            status = "busy"

        # Update thread
        thread.update(
            {
                "updated_at": datetime.now(UTC),
                "values": checkpoint["values"] if checkpoint else None,
                "status": status,
                "interrupts": (
                    {
                        t["id"]: t["interrupts"]
                        for t in checkpoint["tasks"]
                        if t.get("interrupts")
                    }
                    if checkpoint
                    else {}
                ),
            }
        )

    @staticmethod
    async def delete(
        conn: InMemConnectionProto,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[UUID]:
        """Delete a thread by ID and cascade delete all associated runs."""
        thread_list = conn.store["threads"]
        thread_idx = None
        thread_id = _ensure_uuid(thread_id)

        # Find the thread to delete
        for idx, thread in enumerate(thread_list):
            if thread["thread_id"] == thread_id:
                thread_idx = idx
                break
        filters = await Threads.handle_event(
            ctx,
            "delete",
            Auth.types.ThreadsDelete(thread_id=thread_id),
        )
        if (filters and not _check_filter_match(thread["metadata"], filters)) or (
            thread_idx is None
        ):
            raise HTTPException(
                status_code=404, detail=f"Thread with ID {thread_id} not found"
            )
        # Cascade delete all runs associated with this thread
        conn.store["runs"] = [
            run for run in conn.store["runs"] if run["thread_id"] != thread_id
        ]
        _delete_checkpoints_for_thread(thread_id, conn)

        if thread_idx is not None:
            # Remove the thread from the store
            deleted_thread = thread_list.pop(thread_idx)

            # Return an async iterator with the deleted thread_id
            async def id_iterator() -> AsyncIterator[UUID]:
                yield deleted_thread["thread_id"]

            return id_iterator()

        # If thread not found, return empty iterator
        async def empty_iterator() -> AsyncIterator[UUID]:
            if False:  # This ensures the iterator is empty
                yield

        return empty_iterator()

    @staticmethod
    async def copy(
        conn: InMemConnectionProto,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Thread]:
        """Create a copy of an existing thread."""
        thread_id = _ensure_uuid(thread_id)
        new_thread_id = uuid4()
        filters = await Threads.handle_event(
            ctx,
            "read",
            Auth.types.ThreadsRead(
                thread_id=new_thread_id,
            ),
        )
        async with conn.pipeline():
            # Find the original thread in our store
            original_thread = next(
                (t for t in conn.store["threads"] if t["thread_id"] == thread_id), None
            )

            if not original_thread:
                return _empty_generator()
            if filters and not _check_filter_match(
                original_thread["metadata"], filters
            ):
                return _empty_generator()

            # Create new thread with copied metadata
            new_thread: Thread = {
                "thread_id": new_thread_id,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
                "metadata": deepcopy(original_thread["metadata"]),
                "status": "idle",
                "config": {},
            }

            # Add new thread to store
            conn.store["threads"].append(new_thread)

            checkpointer = Checkpointer(conn)
            copied_storage = _replace_thread_id(
                checkpointer.storage[str(thread_id)], new_thread_id, thread_id
            )
            checkpointer.storage[str(new_thread_id)] = copied_storage
            # Copy the writes over (if any)
            outer_keys = []
            for k in checkpointer.writes:
                if k[0] == str(thread_id):
                    outer_keys.append(k)
            for tid, checkpoint_ns, checkpoint_id in outer_keys:
                mapped = {
                    k: _replace_thread_id(v, new_thread_id, thread_id)
                    for k, v in checkpointer.writes[
                        (str(tid), checkpoint_ns, checkpoint_id)
                    ].items()
                }

                checkpointer.writes[
                    (str(new_thread_id), checkpoint_ns, checkpoint_id)
                ] = mapped

            async def row_generator() -> AsyncIterator[Thread]:
                yield new_thread

            return row_generator()

    class State(Authenticated):
        # We will treat this like a runs resource for now.
        resource = "threads"

        @staticmethod
        async def get(
            conn: InMemConnectionProto,
            config: Config,
            subgraphs: bool = False,
            ctx: Auth.types.BaseAuthContext | None = None,
        ) -> StateSnapshot:
            """Get state for a thread."""
            checkpointer = Checkpointer(conn)
            thread_id = _ensure_uuid(config["configurable"]["thread_id"])
            # Auth will be applied here so no need to use filters downstream
            thread_iter = await Threads.get(conn, thread_id, ctx=ctx)
            thread = await anext(thread_iter)
            checkpoint = await checkpointer.aget(config)

            if not thread:
                return StateSnapshot(
                    values={},
                    next=[],
                    config=None,
                    metadata=None,
                    created_at=None,
                    parent_config=None,
                    tasks=tuple(),
                )

            metadata = thread.get("metadata", {})
            thread_config = thread.get("config", {})

            if graph_id := metadata.get("graph_id"):
                # format latest checkpoint for response
                checkpointer.latest_iter = checkpoint
                async with get_graph(
                    graph_id, thread_config, checkpointer=checkpointer
                ) as graph:
                    result = await graph.aget_state(config, subgraphs=subgraphs)
                    if (
                        result.metadata is not None
                        and "checkpoint_ns" in result.metadata
                        and result.metadata["checkpoint_ns"] == ""
                    ):
                        result.metadata.pop("checkpoint_ns")
                    return result
            else:
                return StateSnapshot(
                    values={},
                    next=[],
                    config=None,
                    metadata=None,
                    created_at=None,
                    parent_config=None,
                    tasks=tuple(),
                )

        @staticmethod
        async def post(
            conn: InMemConnectionProto,
            config: Config,
            values: Sequence[dict] | dict[str, Any] | None,
            as_node: str | None = None,
            ctx: Auth.types.BaseAuthContext | None = None,
        ) -> ThreadUpdateResponse:
            """Add state to a thread."""
            thread_id = _ensure_uuid(config["configurable"]["thread_id"])
            filters = await Threads.handle_event(
                ctx,
                "update",
                Auth.types.ThreadsUpdate(thread_id=thread_id),
            )

            checkpointer = Checkpointer(conn)

            thread_iter = await Threads.get(conn, thread_id, ctx=ctx)
            thread = await fetchone(
                thread_iter, not_found_detail=f"Thread {thread_id} not found."
            )
            checkpoint = await checkpointer.aget(config)

            if not thread:
                raise HTTPException(status_code=404, detail="Thread not found")
            if not _check_filter_match(thread["metadata"], filters):
                raise HTTPException(status_code=403, detail="Forbidden")

            metadata = thread["metadata"]
            thread_config = thread["config"]

            if graph_id := metadata.get("graph_id"):
                config["configurable"].setdefault("graph_id", graph_id)

                checkpointer.latest_iter = checkpoint
                async with get_graph(
                    graph_id, thread_config, checkpointer=checkpointer
                ) as graph:
                    update_config = config.copy()
                    update_config["configurable"] = {
                        **config["configurable"],
                        "checkpoint_ns": config["configurable"].get(
                            "checkpoint_ns", ""
                        ),
                    }
                    next_config = await graph.aupdate_state(
                        update_config, values, as_node=as_node
                    )

                    # Get current state
                    state = await Threads.State.get(
                        conn, config, subgraphs=False, ctx=ctx
                    )
                    # Update thread values
                    for thread in conn.store["threads"]:
                        if thread["thread_id"] == thread_id:
                            thread["values"] = state.values
                            break

                    return ThreadUpdateResponse(
                        checkpoint=next_config["configurable"],
                        # Including deprecated fields
                        configurable=next_config["configurable"],
                        checkpoint_id=next_config["configurable"]["checkpoint_id"],
                    )
            else:
                raise HTTPException(status_code=400, detail="Thread has no graph ID.")

        @staticmethod
        async def bulk(
            conn: InMemConnectionProto,
            *,
            config: Config,
            supersteps: Sequence[dict],
            ctx: Auth.types.BaseAuthContext | None = None,
        ) -> ThreadUpdateResponse:
            """Update a thread with a batch of state updates."""

            from langgraph.pregel.types import StateUpdate

            thread_id = _ensure_uuid(config["configurable"]["thread_id"])
            filters = await Threads.handle_event(
                ctx,
                "update",
                Auth.types.ThreadsUpdate(thread_id=thread_id),
            )

            thread_iter = await Threads.get(conn, thread_id, ctx=ctx)
            thread = await fetchone(
                thread_iter, not_found_detail=f"Thread {thread_id} not found."
            )

            thread_config = thread["config"]
            metadata = thread["metadata"]

            if not thread:
                raise HTTPException(status_code=404, detail="Thread not found")

            if not _check_filter_match(metadata, filters):
                raise HTTPException(status_code=403, detail="Forbidden")

            if graph_id := metadata.get("graph_id"):
                config["configurable"].setdefault("graph_id", graph_id)
                config["configurable"].setdefault("checkpoint_ns", "")

                async with get_graph(
                    graph_id, thread_config, checkpointer=Checkpointer(conn)
                ) as graph:
                    next_config = await graph.abulk_update_state(
                        config,
                        [
                            [
                                StateUpdate(
                                    map_cmd(update.get("command"))
                                    if update.get("command")
                                    else update.get("values"),
                                    update.get("as_node"),
                                )
                                for update in superstep.get("updates", [])
                            ]
                            for superstep in supersteps
                        ],
                    )

                    state = await Threads.State.get(
                        conn, config, subgraphs=False, ctx=ctx
                    )

                    # update thread values
                    for thread in conn.store["threads"]:
                        if thread["thread_id"] == thread_id:
                            thread["values"] = state.values
                            break

                    return ThreadUpdateResponse(
                        checkpoint=next_config["configurable"],
                    )
            else:
                raise HTTPException(status_code=400, detail="Thread has no graph ID")

        @staticmethod
        async def list(
            conn: InMemConnectionProto,
            *,
            config: Config,
            limit: int = 10,
            before: str | Checkpoint | None = None,
            metadata: MetadataInput = None,
            ctx: Auth.types.BaseAuthContext | None = None,
        ) -> list[StateSnapshot]:
            """Get the history of a thread."""

            thread_id = _ensure_uuid(config["configurable"]["thread_id"])
            thread = None
            filters = await Threads.handle_event(
                ctx,
                "read",
                Auth.types.ThreadsRead(thread_id=thread_id),
            )
            thread = await fetchone(
                await Threads.get(conn, config["configurable"]["thread_id"], ctx=ctx)
            )

            # Parse thread metadata and config
            thread_metadata = thread["metadata"]
            if not _check_filter_match(thread_metadata, filters):
                return []

            thread_config = thread["config"]
            # If graph_id exists, get state history
            if graph_id := thread_metadata.get("graph_id"):
                async with get_graph(
                    graph_id, thread_config, checkpointer=Checkpointer(conn)
                ) as graph:
                    # Convert before parameter if it's a string
                    before_param = (
                        {"configurable": {"checkpoint_id": before}}
                        if isinstance(before, str)
                        else before
                    )

                    states = [
                        state
                        async for state in graph.aget_state_history(
                            config, limit=limit, filter=metadata, before=before_param
                        )
                    ]

                    return states

            return []


RUN_LOCK = asyncio.Lock()


class Runs(Authenticated):
    resource = "threads"

    @staticmethod
    async def stats(conn: InMemConnectionProto) -> QueueStats:
        """Get stats about the queue."""
        pending_runs = [run for run in conn.store["runs"] if run["status"] == "pending"]
        running_runs = [run for run in conn.store["runs"] if run["status"] == "running"]

        if not pending_runs and not running_runs:
            return {
                "n_pending": 0,
                "max_age_secs": None,
                "med_age_secs": None,
                "n_running": 0,
            }

        # Get all creation timestamps
        created_times = [run.get("created_at") for run in (pending_runs + running_runs)]
        created_times = [
            t for t in created_times if t is not None
        ]  # Filter out None values

        if not created_times:
            return {
                "n_pending": len(pending_runs),
                "n_running": len(running_runs),
                "max_age_secs": None,
                "med_age_secs": None,
            }

        # Find oldest (max age)
        oldest_time = min(created_times)  # Earliest timestamp = oldest run

        # Find median age
        sorted_times = sorted(created_times)
        median_idx = len(sorted_times) // 2
        median_time = sorted_times[median_idx]

        return {
            "n_pending": len(pending_runs),
            "n_running": len(running_runs),
            "max_age_secs": oldest_time,
            "med_age_secs": median_time,
        }

    @staticmethod
    async def next(wait: bool, limit: int = 1) -> AsyncIterator[tuple[Run, int]]:
        """Get the next run from the queue, and the attempt number.
        1 is the first attempt, 2 is the first retry, etc."""
        now = datetime.now(UTC)

        if wait:
            await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(0)

        async with connect() as conn, RUN_LOCK:
            pending_runs = sorted(
                [
                    run
                    for run in conn.store["runs"]
                    if run["status"] == "pending" and run.get("created_at", now) < now
                ],
                key=lambda x: x.get("created_at", datetime.min),
            )

            if not pending_runs:
                return

            # Try to lock and get the first available run
            for _, run in zip(range(limit), pending_runs, strict=False):
                if run["status"] != "pending":
                    continue

                run_id = run["run_id"]
                thread_id = run["thread_id"]
                thread = next(
                    (t for t in conn.store["threads"] if t["thread_id"] == thread_id),
                    None,
                )

                if thread is None:
                    await logger.awarning(
                        "Unexpected missing thread in Runs.next",
                        thread_id=run["thread_id"],
                    )
                    continue

                if run["status"] != "pending":
                    continue

                if any(
                    run["status"] == "running"
                    for run in conn.store["runs"]
                    if run["thread_id"] == thread_id
                ):
                    continue
                # Increment attempt counter
                attempt = await conn.retry_counter.increment(run_id)
                # Set run as "running"
                run["status"] = "running"
                yield run, attempt

    @asynccontextmanager
    @staticmethod
    async def enter(
        run_id: UUID, loop: asyncio.AbstractEventLoop
    ) -> AsyncIterator[ValueEvent]:
        """Enter a run, listen for cancellation while running, signal when done."
        This method should be called as a context manager by a worker executing a run.
        """
        stream_manager = get_stream_manager()
        # Get queue for this run
        queue = await Runs.Stream.subscribe(run_id)

        async with SimpleTaskGroup(cancel=True) as tg:
            done = ValueEvent()
            tg.create_task(listen_for_cancellation(queue, run_id, done))

            # Give done event to caller
            yield done
            # Signal done to all subscribers
            control_message = Message(
                topic=f"run:{run_id}:control".encode(), data=b"done"
            )

            # Store the control message for late subscribers
            await stream_manager.put(run_id, control_message)
            stream_manager.control_queues[run_id].append(control_message)
            # Clean up this queue
            await stream_manager.remove_queue(run_id, queue)

    @staticmethod
    async def sweep(conn: InMemConnectionProto) -> list[UUID]:
        """Sweep runs that are no longer running"""
        return []

    @staticmethod
    def _merge_jsonb(*objects: dict) -> dict:
        """Mimics PostgreSQL's JSONB merge behavior"""
        result = {}
        for obj in objects:
            if obj is not None:
                result.update(copy.deepcopy(obj))
        return result

    @staticmethod
    def _get_configurable(config: dict) -> dict:
        """Extract configurable from config, mimicking PostgreSQL's coalesce"""
        return config.get("configurable", {})

    @staticmethod
    async def put(
        conn: InMemConnectionProto,
        assistant_id: UUID,
        kwargs: dict,
        *,
        thread_id: UUID | None = None,
        user_id: str | None = None,
        run_id: UUID | None = None,
        status: RunStatus | None = "pending",
        metadata: MetadataInput,
        prevent_insert_if_inflight: bool,
        multitask_strategy: MultitaskStrategy = "reject",
        if_not_exists: IfNotExists = "reject",
        after_seconds: int = 0,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Run]:
        """Create a run."""
        assistant_id = _ensure_uuid(assistant_id)
        assistant = next(
            (a for a in conn.store["assistants"] if a["assistant_id"] == assistant_id),
            None,
        )

        if not assistant:
            return _empty_generator()

        thread_id = _ensure_uuid(thread_id) if thread_id else None
        run_id = _ensure_uuid(run_id) if run_id else None
        metadata = metadata if metadata is not None else {}
        config = kwargs.get("config", {})

        # Handle thread creation/update
        existing_thread = next(
            (t for t in conn.store["threads"] if t["thread_id"] == thread_id), None
        )
        filters = await Runs.handle_event(
            ctx,
            "create_run",
            Auth.types.RunsCreate(
                thread_id=thread_id,
                assistant_id=assistant_id,
                run_id=run_id,
                status=status,
                metadata=metadata,
                prevent_insert_if_inflight=prevent_insert_if_inflight,
                multitask_strategy=multitask_strategy,
                if_not_exists=if_not_exists,
                after_seconds=after_seconds,
                kwargs=kwargs,
            ),
        )
        if existing_thread and filters:
            # Reject if the user doesn't own the thread
            if not _check_filter_match(existing_thread["metadata"], filters):
                return _empty_generator()

        if not existing_thread and (thread_id is None or if_not_exists == "create"):
            # Create new thread
            if thread_id is None:
                thread_id = uuid4()
            thread = Thread(
                thread_id=thread_id,
                status="busy",
                metadata={
                    "graph_id": assistant["graph_id"],
                    "assistant_id": str(assistant_id),
                    **metadata,
                },
                config=Runs._merge_jsonb(
                    assistant["config"],
                    config,
                    {
                        "configurable": Runs._merge_jsonb(
                            Runs._get_configurable(assistant["config"]),
                            Runs._get_configurable(config),
                        )
                    },
                ),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            await logger.ainfo("Creating thread", thread_id=thread_id)
            conn.store["threads"].append(thread)
        elif existing_thread:
            # Update existing thread
            if existing_thread["status"] != "busy":
                existing_thread["status"] = "busy"
                existing_thread["metadata"] = Runs._merge_jsonb(
                    existing_thread["metadata"],
                    {
                        "graph_id": assistant["graph_id"],
                        "assistant_id": str(assistant_id),
                    },
                )
                existing_thread["config"] = Runs._merge_jsonb(
                    assistant["config"],
                    existing_thread["config"],
                    config,
                    {
                        "configurable": Runs._merge_jsonb(
                            Runs._get_configurable(assistant["config"]),
                            Runs._get_configurable(existing_thread["config"]),
                            Runs._get_configurable(config),
                        )
                    },
                )
                existing_thread["updated_at"] = datetime.now(UTC)
        else:
            return _empty_generator()

        # Check for inflight runs if needed
        inflight_runs = [
            r
            for r in conn.store["runs"]
            if r["thread_id"] == thread_id and r["status"] in ("pending", "running")
        ]
        if prevent_insert_if_inflight:
            if inflight_runs:

                async def _return_inflight():
                    for run in inflight_runs:
                        yield run

                return _return_inflight()

        # Create new run
        configurable = Runs._merge_jsonb(
            Runs._get_configurable(assistant["config"]),
            (
                Runs._get_configurable(existing_thread["config"])
                if existing_thread
                else {}
            ),
            Runs._get_configurable(config),
            {
                "run_id": str(run_id),
                "thread_id": str(thread_id),
                "graph_id": assistant["graph_id"],
                "assistant_id": str(assistant_id),
                "user_id": (
                    config.get("configurable", {}).get("user_id")
                    or (
                        existing_thread["config"].get("configurable", {}).get("user_id")
                        if existing_thread
                        else None
                    )
                    or assistant["config"].get("configurable", {}).get("user_id")
                    or user_id
                ),
            },
        )
        merged_metadata = Runs._merge_jsonb(
            assistant["metadata"],
            existing_thread["metadata"] if existing_thread else {},
            metadata,
        )
        new_run = Run(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            metadata=merged_metadata,
            status=status,
            kwargs=Runs._merge_jsonb(
                kwargs,
                {
                    "config": Runs._merge_jsonb(
                        assistant["config"],
                        config,
                        {"configurable": configurable},
                        {
                            "metadata": merged_metadata,
                        },
                    )
                },
            ),
            multitask_strategy=multitask_strategy,
            created_at=datetime.now(UTC) + timedelta(seconds=after_seconds),
            updated_at=datetime.now(UTC),
        )
        conn.store["runs"].append(new_run)

        async def _yield_new():
            yield new_run
            for r in inflight_runs:
                yield r

        return _yield_new()

    @staticmethod
    async def get(
        conn: InMemConnectionProto,
        run_id: UUID,
        *,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Run]:
        """Get a run by ID."""

        run_id, thread_id = _ensure_uuid(run_id), _ensure_uuid(thread_id)
        filters = await Runs.handle_event(
            ctx,
            "read",
            Auth.types.ThreadsRead(thread_id=thread_id),
        )

        async def _yield_result():
            matching_run = None
            for run in conn.store["runs"]:
                if run["run_id"] == run_id and run["thread_id"] == thread_id:
                    matching_run = run
                    break
            if matching_run:
                if filters:
                    thread = await Threads._get_with_filters(
                        conn, matching_run["thread_id"], filters
                    )
                    if not thread:
                        return
                yield matching_run

        return _yield_result()

    @staticmethod
    async def delete(
        conn: InMemConnectionProto,
        run_id: UUID,
        *,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[UUID]:
        """Delete a run by ID."""
        run_id, thread_id = _ensure_uuid(run_id), _ensure_uuid(thread_id)
        filters = await Runs.handle_event(
            ctx,
            "delete",
            Auth.types.ThreadsDelete(run_id=run_id, thread_id=thread_id),
        )

        if filters:
            thread = await Threads._get_with_filters(conn, thread_id, filters)
            if not thread:
                return _empty_generator()
        _delete_checkpoints_for_thread(thread_id, conn, run_id=run_id)
        found = False
        for i, run in enumerate(conn.store["runs"]):
            if run["run_id"] == run_id and run["thread_id"] == thread_id:
                del conn.store["runs"][i]
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="Run not found")

        async def _yield_deleted():
            await logger.ainfo("Run deleted", run_id=run_id)
            yield run_id

        return _yield_deleted()

    @staticmethod
    async def join(
        run_id: UUID,
        *,
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> Fragment:
        """Wait for a run to complete. If already done, return immediately.

        Returns:
            the final state of the run.
        """
        async with connect() as conn:
            # Validate ownership
            thread_iter = await Threads.get(conn, thread_id, ctx=ctx)
            await fetchone(thread_iter)
        last_chunk: bytes | None = None
        # wait for the run to complete
        # Rely on this join's auth
        async for mode, chunk in Runs.Stream.join(
            run_id, thread_id=thread_id, stream_mode="values", ctx=ctx, ignore_404=True
        ):
            if mode == b"values":
                last_chunk = chunk
        # if we received a final chunk, return it
        if last_chunk is not None:
            # ie. if the run completed while we were waiting for it
            return Fragment(last_chunk)
        else:
            # otherwise, the run had already finished, so fetch the state from thread
            async with connect() as conn:
                thread_iter = await Threads.get(conn, thread_id, ctx=ctx)
                thread = await fetchone(thread_iter)
                return thread["values"]

    @staticmethod
    async def cancel(
        conn: InMemConnectionProto,
        run_ids: Sequence[UUID],
        *,
        action: Literal["interrupt", "rollback"] = "interrupt",
        thread_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> None:
        """Cancel a run."""
        # Authwise, this invokes the runs.update handler
        # Cancellation tries to take two actions, to cover runs in different states:
        # - For any run, send a cancellation message through the stream manager
        # - For queued runs not yet picked up by a worker, update their status if interrupt,
        #       delete if rollback.
        # - For runs currently being worked on, the worker will handle cancellation
        # - For runs in any other state, we raise a 404
        run_ids = [_ensure_uuid(run_id) for run_id in run_ids]
        thread_id = _ensure_uuid(thread_id)
        filters = await Runs.handle_event(
            ctx,
            "update",
            Auth.types.ThreadsUpdate(
                thread_id=thread_id,
                action=action,
                metadata={"run_ids": run_ids},
            ),
        )

        stream_manager = get_stream_manager()
        found_runs = []
        coros = []
        for run_id in run_ids:
            run = next(
                (
                    r
                    for r in conn.store["runs"]
                    if r["run_id"] == run_id and r["thread_id"] == thread_id
                ),
                None,
            )
            if run:
                if filters:
                    thread = await Threads._get_with_filters(conn, thread_id, filters)
                    if not thread:
                        continue
                found_runs.append(run)
                # Send cancellation message through stream manager
                control_message = Message(
                    topic=f"run:{run_id}:control".encode(),
                    data=action.encode(),
                )
                queues = stream_manager.get_queues(run_id)
                coros.append(stream_manager.put(run_id, control_message))

                # Update status for pending runs
                if run["status"] in ("pending", "running"):
                    if queues or action != "rollback":
                        run["status"] = "interrupted"
                        run["updated_at"] = datetime.now(tz=UTC)
                    else:
                        await logger.ainfo(
                            "Eagerly deleting unscheduled run with rollback action",
                            run_id=run_id,
                            thread_id=thread_id,
                        )
                        coros.append(Runs.delete(conn, run_id, thread_id=thread_id))

                else:
                    await logger.awarning(
                        "Attempted to cancel non-pending run.",
                        run_id=run_id,
                        status=run["status"],
                    )
        if coros:
            await asyncio.gather(*coros)
        if len(found_runs) == len(run_ids):
            await logger.ainfo(
                "Cancelled runs",
                run_ids=[str(run_id) for run_id in run_ids],
                thread_id=str(thread_id),
                action=action,
            )
        else:
            raise HTTPException(status_code=404, detail="Run not found")

    @staticmethod
    async def search(
        conn: InMemConnectionProto,
        thread_id: UUID,
        *,
        limit: int = 10,
        offset: int = 0,
        metadata: MetadataInput,
        status: RunStatus | None = None,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Run]:
        """List all runs by thread."""
        runs = conn.store["runs"]
        metadata = metadata if metadata is not None else {}
        thread_id = _ensure_uuid(thread_id)
        filters = await Runs.handle_event(
            ctx,
            "search",
            Auth.types.ThreadsSearch(thread_id=thread_id, metadata=metadata),
        )
        filtered_runs = [
            run
            for run in runs
            if run["thread_id"] == thread_id
            and is_jsonb_contained(run["metadata"], metadata)
            and (
                not filters
                or (await Threads._get_with_filters(conn, thread_id, filters))
            )
            and (status is None or run["status"] == status)
        ]
        sorted_runs = sorted(filtered_runs, key=lambda x: x["created_at"], reverse=True)
        sliced_runs = sorted_runs[offset : offset + limit]

        async def _return():
            for run in sliced_runs:
                yield run

        return _return()

    @staticmethod
    async def set_status(
        conn: InMemConnectionProto, run_id: UUID, status: RunStatus
    ) -> None:
        """Set the status of a run."""
        # Find the run in the store
        run_id = _ensure_uuid(run_id)
        run = next((run for run in conn.store["runs"] if run["run_id"] == run_id), None)

        if run:
            # Update the status and updated_at timestamp
            run["status"] = status
            run["updated_at"] = datetime.now(tz=UTC)
            return run
        return None

    class Stream:
        @staticmethod
        async def subscribe(
            run_id: UUID,
            *,
            stream_mode: "StreamMode | None" = None,
        ) -> asyncio.Queue:
            """Subscribe to the run stream, returning a queue."""
            stream_manager = get_stream_manager()
            queue = await stream_manager.add_queue(_ensure_uuid(run_id))

            # If there's a control message already stored, send it to the new subscriber
            if control_messages := stream_manager.control_queues.get(run_id):
                for control_msg in control_messages:
                    await queue.put(control_msg)
            return queue

        @staticmethod
        async def join(
            run_id: UUID,
            *,
            thread_id: UUID,
            ignore_404: bool = False,
            cancel_on_disconnect: bool = False,
            stream_mode: "StreamMode | asyncio.Queue | None" = None,
            ctx: Auth.types.BaseAuthContext | None = None,
        ) -> AsyncIterator[tuple[bytes, bytes]]:
            """Stream the run output."""
            log = logger.isEnabledFor(logging.DEBUG)
            queue = (
                stream_mode
                if isinstance(stream_mode, asyncio.Queue)
                else await Runs.Stream.subscribe(run_id, stream_mode=stream_mode)
            )

            try:
                async with connect() as conn:
                    filters = await Runs.handle_event(
                        ctx,
                        "read",
                        Auth.types.ThreadsRead(thread_id=thread_id),
                    )
                    if filters:
                        thread = await Threads._get_with_filters(
                            cast(InMemConnectionProto, conn), thread_id, filters
                        )
                        if not thread:
                            raise WrappedHTTPException(
                                HTTPException(
                                    status_code=404, detail="Thread not found"
                                )
                            )
                    channel_prefix = f"run:{run_id}:stream:"
                    len_prefix = len(channel_prefix.encode())

                    while True:
                        try:
                            # Wait for messages with a timeout
                            message = await asyncio.wait_for(queue.get(), timeout=0.5)
                            topic, data = message.topic, message.data

                            if topic.decode() == f"run:{run_id}:control":
                                if data == b"done":
                                    break
                            else:
                                # Extract mode from topic
                                yield topic[len_prefix:], data
                                if log:
                                    await logger.adebug(
                                        "Streamed run event",
                                        run_id=str(run_id),
                                        stream_mode=topic[len_prefix:],
                                        data=data,
                                    )
                        except TimeoutError:
                            # Check if the run is still pending
                            run_iter = await Runs.get(
                                conn, run_id, thread_id=thread_id, ctx=ctx
                            )
                            run = await anext(run_iter, None)

                            if ignore_404 and run is None:
                                break
                            elif run is None:
                                yield (
                                    b"error",
                                    HTTPException(
                                        status_code=404, detail="Run not found"
                                    ),
                                )
                                break
                            elif run["status"] not in ("pending", "running"):
                                break
            except WrappedHTTPException as e:
                raise e.http_exception from None
            except:
                if cancel_on_disconnect:
                    create_task(cancel_run(thread_id, run_id))
                raise
            finally:
                stream_manager = get_stream_manager()
                await stream_manager.remove_queue(run_id, queue)

        @staticmethod
        async def publish(
            run_id: UUID,
            event: str,
            message: bytes,
        ) -> None:
            """Publish a message to all subscribers of the run stream."""
            topic = f"run:{run_id}:stream:{event}".encode()

            stream_manager = get_stream_manager()
            # Send to all queues subscribed to this run_id
            await stream_manager.put(run_id, Message(topic=topic, data=message))


async def listen_for_cancellation(
    queue: asyncio.Queue, run_id: UUID, done: "ValueEvent"
):
    """Listen for cancellation messages and set the done event accordingly."""
    stream_manager = get_stream_manager()
    control_key = f"run:{run_id}:control"

    if existing_queue := stream_manager.control_queues.get(run_id):
        for message in existing_queue:
            payload = message.data
            if payload == b"rollback":
                done.set(UserRollback())
            elif payload == b"interrupt":
                done.set(UserInterrupt())

    while not done.is_set():
        try:
            # This task gets cancelled when Runs.enter exits anyway,
            # so we can have a pretty length timeout here
            message = await asyncio.wait_for(queue.get(), timeout=240)
            payload = message.data
            if payload == b"rollback":
                done.set(UserRollback())
            elif payload == b"interrupt":
                done.set(UserInterrupt())
            elif payload == b"done":
                done.set()
                break

            # Store control messages for late subscribers
            if message.topic.decode() == control_key:
                stream_manager.control_queues[run_id].append(message)
        except TimeoutError:
            break


class Crons:
    @staticmethod
    async def put(
        conn: InMemConnectionProto,
        *,
        payload: dict,
        schedule: str,
        cron_id: UUID | None = None,
        thread_id: UUID | None = None,
        end_time: datetime | None = None,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Cron]:
        raise NotImplementedError

    @staticmethod
    async def delete(
        conn: InMemConnectionProto,
        cron_id: UUID,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[UUID]:
        raise NotImplementedError

    @staticmethod
    async def next(
        conn: InMemConnectionProto,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Cron]:
        yield
        raise NotImplementedError("The in-mem server does not implement Crons.")

    @staticmethod
    async def set_next_run_date(
        conn: InMemConnectionProto,
        cron_id: UUID,
        next_run_date: datetime,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> None:
        raise NotImplementedError

    @staticmethod
    async def search(
        conn: InMemConnectionProto,
        *,
        assistant_id: UUID | None,
        thread_id: UUID | None,
        limit: int,
        offset: int,
        ctx: Auth.types.BaseAuthContext | None = None,
    ) -> AsyncIterator[Cron]:
        raise NotImplementedError


async def cancel_run(
    thread_id: UUID, run_id: UUID, ctx: Auth.types.BaseAuthContext | None = None
) -> None:
    async with connect() as conn:
        await Runs.cancel(conn, [run_id], thread_id=thread_id, ctx=ctx)


def _delete_checkpoints_for_thread(
    thread_id: str | UUID,
    conn: InMemConnectionProto,
    run_id: str | UUID | None = None,
):
    checkpointer = Checkpointer(conn)
    thread_id = str(thread_id)
    if thread_id not in checkpointer.storage:
        return
    if run_id:
        # Look through metadata
        run_id = str(run_id)
        for checkpoint_ns, checkpoints in list(checkpointer.storage[thread_id].items()):
            for checkpoint_id, (_, metadata_b, _) in list(checkpoints.items()):
                metadata = checkpointer.serde.loads_typed(metadata_b)
                if metadata.get("run_id") == run_id:
                    del checkpointer.storage[thread_id][checkpoint_ns][checkpoint_id]
                    if not checkpointer.storage[thread_id][checkpoint_ns]:
                        del checkpointer.storage[thread_id][checkpoint_ns]
    else:
        del checkpointer.storage[thread_id]
        # Keys are (thread_id, checkpoint_ns, checkpoint_id)
        checkpointer.writes = defaultdict(
            dict, {k: v for k, v in checkpointer.writes.items() if k[0] != thread_id}
        )


def _check_filter_match(metadata: dict, filters: Auth.types.FilterType | None) -> bool:
    """Check if metadata matches the filter conditions.

    Args:
        metadata: The metadata to check
        filters: The filter conditions to apply

    Returns:
        True if the metadata matches all filter conditions, False otherwise
    """
    if not filters:
        return True

    for key, value in filters.items():
        if isinstance(value, dict):
            op = next(iter(value))
            filter_value = value[op]

            if op == "$eq":
                if key not in metadata or metadata[key] != filter_value:
                    return False
            elif op == "$contains":
                if (
                    key not in metadata
                    or not isinstance(metadata[key], list)
                    or filter_value not in metadata[key]
                ):
                    return False
        else:
            # Direct equality
            if key not in metadata or metadata[key] != value:
                return False

    return True


async def _empty_generator():
    if False:
        yield


__all__ = [
    "Assistants",
    "Crons",
    "Runs",
    "Threads",
]
