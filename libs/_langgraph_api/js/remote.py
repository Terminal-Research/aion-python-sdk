import asyncio
import logging
import os
import shutil
import ssl
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from typing import Any, Literal

import certifi
import httpx
import orjson
import structlog
import uvicorn
from langchain_core.runnables.config import RunnableConfig
from langchain_core.runnables.graph import Edge, Node
from langchain_core.runnables.graph import Graph as DrawableGraph
from langchain_core.runnables.schema import (
    CustomStreamEvent,
    StandardStreamEvent,
    StreamEvent,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.pregel.types import PregelTask, StateSnapshot
from langgraph.store.base import GetOp, Item, ListNamespacesOp, PutOp, SearchOp
from langgraph.types import Command, Interrupt
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Route

from langgraph_api.js.base import BaseRemotePregel
from langgraph_api.js.errors import RemoteException
from langgraph_api.js.sse import SSEDecoder, aiter_lines_raw
from langgraph_api.route import ApiResponse
from langgraph_api.serde import json_dumpb
from langgraph_api.utils import AsyncConnectionProto

logger = structlog.stdlib.get_logger(__name__)

GRAPH_PORT = 5556
REMOTE_PORT = 5555
SSL = ssl.create_default_context(cafile=certifi.where())

if port := int(os.getenv("PORT", "8080")):
    if port in (GRAPH_PORT, REMOTE_PORT):
        raise ValueError(
            f"PORT={port} is a reserved port for the JS worker. Please choose a different port."
        )

_client = httpx.AsyncClient(
    base_url=f"http://localhost:{GRAPH_PORT}",
    timeout=httpx.Timeout(15.0),  # 3 x HEARTBEAT_MS
    limits=httpx.Limits(),
    transport=httpx.AsyncHTTPTransport(verify=SSL),
)


async def _client_stream(method: str, data: dict[str, Any]):
    graph_id = data.get("graph_id")
    async with _client.stream(
        "POST",
        f"/{graph_id}/{method}",
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-store",
            "Content-Type": "application/json",
        },
        data=orjson.dumps(data),
    ) as response:
        decoder = SSEDecoder()
        async for line in aiter_lines_raw(response):
            sse = decoder.decode(line)
            if sse is not None:
                if sse.event == "error":
                    raise RemoteException(sse.data["error"], sse.data["message"])
                yield sse.data


async def _client_invoke(method: str, data: dict[str, Any]):
    graph_id = data.get("graph_id")
    res = await _client.post(
        f"/{graph_id}/{method}",
        headers={"Content-Type": "application/json"},
        data=orjson.dumps(data),
    )
    return res.json()


class RemotePregel(BaseRemotePregel):
    @staticmethod
    def load(graph_id: str):
        model = RemotePregel()
        model.graph_id = graph_id
        return model

    async def astream_events(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        version: Literal["v1", "v2"],
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if version != "v2":
            raise ValueError("Only v2 of astream_events is supported")

        data = {
            "graph_id": self.graph_id,
            "command" if isinstance(input, Command) else "input": input,
            "config": config,
            **kwargs,
        }

        async for event in _client_stream("streamEvents", data):
            if event["event"] == "on_custom_event":
                yield CustomStreamEvent(**event)
            else:
                yield StandardStreamEvent(**event)

    async def fetch_state_schema(self):
        return await _client_invoke("getSchema", {"graph_id": self.graph_id})

    async def fetch_graph(
        self,
        config: RunnableConfig | None = None,
        *,
        xray: int | bool = False,
    ) -> DrawableGraph:
        response = await _client_invoke(
            "getGraph", {"graph_id": self.graph_id, "config": config, "xray": xray}
        )

        nodes: list[Any] = response.pop("nodes")
        edges: list[Any] = response.pop("edges")

        class NoopModel(BaseModel):
            pass

        return DrawableGraph(
            {
                data["id"]: Node(
                    data["id"], data["id"], NoopModel(), data.get("metadata")
                )
                for data in nodes
            },
            {
                Edge(
                    data["source"],
                    data["target"],
                    data.get("data"),
                    data.get("conditional", False),
                )
                for data in edges
            },
        )

    async def fetch_subgraphs(
        self, *, namespace: str | None = None, recurse: bool = False
    ) -> dict[str, dict]:
        return await _client_invoke(
            "getSubgraphs",
            {"graph_id": self.graph_id, "namespace": namespace, "recurse": recurse},
        )

    def _convert_state_snapshot(self, item: dict) -> StateSnapshot:
        def _convert_tasks(tasks: list[dict]) -> tuple[PregelTask, ...]:
            result: list[PregelTask] = []
            for task in tasks:
                state = task.get("state")

                if state and isinstance(state, dict) and "config" in state:
                    state = self._convert_state_snapshot(state)

                result.append(
                    PregelTask(
                        task["id"],
                        task["name"],
                        tuple(task["path"]) if task.get("path") else tuple(),
                        # TODO: figure out how to properly deserialise errors
                        task.get("error"),
                        (
                            tuple(
                                Interrupt(
                                    value=interrupt["value"],
                                    when=interrupt["when"],
                                    resumable=interrupt.get("resumable", True),
                                    ns=interrupt.get("ns"),
                                )
                                for interrupt in task.get("interrupts")
                            )
                            if task.get("interrupts")
                            else []
                        ),
                        state,
                    )
                )
            return tuple(result)

        return StateSnapshot(
            item.get("values"),
            item.get("next"),
            item.get("config"),
            item.get("metadata"),
            item.get("createdAt"),
            item.get("parentConfig"),
            _convert_tasks(item.get("tasks", [])),
        )

    async def aget_state(
        self, config: RunnableConfig, *, subgraphs: bool = False
    ) -> StateSnapshot:
        return self._convert_state_snapshot(
            await _client_invoke(
                "getState",
                {"graph_id": self.graph_id, "config": config, "subgraphs": subgraphs},
            )
        )

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: dict[str, Any] | Any,
        as_node: str | None = None,
    ) -> RunnableConfig:
        response = await _client_invoke(
            "updateState",
            {
                "graph_id": self.graph_id,
                "config": config,
                "values": values,
                "as_node": as_node,
            },
        )
        return RunnableConfig(**response)

    async def aget_state_history(
        self,
        config: RunnableConfig,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[StateSnapshot]:
        async for event in _client_stream(
            "getStateHistory",
            {
                "graph_id": self.graph_id,
                "config": config,
                "limit": limit,
                "filter": filter,
                "before": before,
            },
        ):
            yield self._convert_state_snapshot(event)

    def get_graph(
        self,
        config: RunnableConfig | None = None,
        *,
        xray: int | bool = False,
    ) -> dict[str, Any]:
        raise Exception("Not implemented")

    def get_input_schema(self, config: RunnableConfig | None = None) -> type[BaseModel]:
        raise Exception("Not implemented")

    def get_output_schema(
        self, config: RunnableConfig | None = None
    ) -> type[BaseModel]:
        raise Exception("Not implemented")

    def config_schema(self) -> type[BaseModel]:
        raise Exception("Not implemented")

    async def invoke(self, input: Any, config: RunnableConfig | None = None):
        raise Exception("Not implemented")


async def run_js_process(paths_str: str, watch: bool = False):
    # check if tsx is available
    tsx_path = shutil.which("tsx")
    if tsx_path is None:
        raise FileNotFoundError("tsx not found in PATH")
    attempt = 0
    while not asyncio.current_task().cancelled():
        client_file = os.path.join(os.path.dirname(__file__), "client.mts")
        args = ("tsx", client_file)
        if watch:
            args = ("tsx", "watch", client_file, "--skip-schema-cache")
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                env={
                    "LANGSERVE_GRAPHS": paths_str,
                    "LANGCHAIN_CALLBACKS_BACKGROUND": "true",
                    "NODE_ENV": "development" if watch else "production",
                    "CHOKIDAR_USEPOLLING": "true",
                    **os.environ,
                },
            )
            code = await process.wait()
            raise Exception(f"JS process exited with code {code}")
        except asyncio.CancelledError:
            logger.info("Terminating JS graphs process")
            try:
                process.terminate()
                await process.wait()
            except (UnboundLocalError, ProcessLookupError):
                pass
            raise
        except Exception:
            if attempt >= 3:
                raise
            else:
                logger.warning(f"Retrying JS process {3 - attempt} more times...")
                attempt += 1


def _get_passthrough_checkpointer(conn: AsyncConnectionProto):
    from langgraph_storage.checkpoint import Checkpointer

    class PassthroughSerialiser(SerializerProtocol):
        def dumps(self, obj: Any) -> bytes:
            return json_dumpb(obj)

        def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
            return "json", json_dumpb(obj)

        def loads(self, data: bytes) -> Any:
            return orjson.loads(data)

        def loads_typed(self, data: tuple[str, bytes]) -> Any:
            type, payload = data
            if type != "json":
                raise ValueError(f"Unsupported type {type}")
            return orjson.loads(payload)

    checkpointer = Checkpointer(conn)

    # This checkpointer does not attempt to revive LC-objects.
    # Instead, it will pass through the JSON values as-is.
    checkpointer.serde = PassthroughSerialiser()

    return checkpointer


def _get_passthrough_store():
    from langgraph_storage.store import Store

    return Store()


# Setup a HTTP server on top of CHECKPOINTER_SOCKET unix socket
# used by `client.mts` to communicate with the Python checkpointer
async def run_remote_checkpointer():
    from langgraph_storage.database import connect

    async def checkpointer_list(payload: dict):
        """Search checkpoints"""

        result = []
        async with connect() as conn:
            checkpointer = _get_passthrough_checkpointer(conn)
            async for item in checkpointer.alist(
                config=payload.get("config"),
                limit=payload.get("limit"),
                before=payload.get("before"),
                filter=payload.get("filter"),
            ):
                result.append(item)

        return result

    async def checkpointer_put(payload: dict):
        """Put the new checkpoint metadata"""

        async with connect() as conn:
            checkpointer = _get_passthrough_checkpointer(conn)
            return await checkpointer.aput(
                payload["config"],
                payload["checkpoint"],
                payload["metadata"],
                payload.get("new_versions", {}),
            )

    async def checkpointer_get_tuple(payload: dict):
        """Get actual checkpoint values (reads)"""

        async with connect() as conn:
            checkpointer = _get_passthrough_checkpointer(conn)
            return await checkpointer.aget_tuple(config=payload["config"])

    async def checkpointer_put_writes(payload: dict):
        """Put actual checkpoint values (writes)"""

        async with connect() as conn:
            checkpointer = _get_passthrough_checkpointer(conn)
            return await checkpointer.aput_writes(
                payload["config"],
                payload["writes"],
                payload["taskId"],
            )

    async def store_batch(payload: dict):
        """Batch operations on the store"""
        operations = payload.get("operations", [])

        if not operations:
            raise ValueError("No operations provided")

        # Convert raw operations to proper objects
        processed_operations = []
        for op in operations:
            if "value" in op:
                processed_operations.append(
                    PutOp(
                        namespace=tuple(op["namespace"]),
                        key=op["key"],
                        value=op["value"],
                    )
                )
            elif "namespace_prefix" in op:
                processed_operations.append(
                    SearchOp(
                        namespace_prefix=tuple(op["namespace_prefix"]),
                        filter=op.get("filter"),
                        limit=op.get("limit", 10),
                        offset=op.get("offset", 0),
                    )
                )

            elif "namespace" in op and "key" in op:
                processed_operations.append(
                    GetOp(namespace=tuple(op["namespace"]), key=op["key"])
                )
            elif "match_conditions" in op:
                processed_operations.append(
                    ListNamespacesOp(
                        match_conditions=tuple(op["match_conditions"]),
                        max_depth=op.get("max_depth"),
                        limit=op.get("limit", 100),
                        offset=op.get("offset", 0),
                    )
                )
            else:
                raise ValueError(f"Unknown operation type: {op}")

        store = _get_passthrough_store()
        results = await store.abatch(processed_operations)

        # Handle potentially undefined or non-dict results
        processed_results = []
        # Result is of type: Union[Item, list[Item], list[tuple[str, ...]], None]
        for result in results:
            if isinstance(result, Item):
                processed_results.append(result.dict())
            elif isinstance(result, dict):
                processed_results.append(result)
            elif isinstance(result, list):
                coerced = []
                for res in result:
                    if isinstance(res, Item):
                        coerced.append(res.dict())
                    elif isinstance(res, tuple):
                        coerced.append(list(res))
                    elif res is None:
                        coerced.append(res)
                    else:
                        coerced.append(str(res))
                processed_results.append(coerced)
            elif result is None:
                processed_results.append(None)
            else:
                processed_results.append(str(result))
        return processed_results

    async def store_get(payload: dict):
        """Get store data"""
        namespaces_str = payload.get("namespace")
        key = payload.get("key")

        if not namespaces_str or not key:
            raise ValueError("Both namespaces and key are required")

        namespaces = namespaces_str.split(".")

        store = _get_passthrough_store()
        result = await store.aget(namespaces, key)

        return result

    async def store_put(payload: dict):
        """Put the new store data"""

        namespace = tuple(payload["namespace"].split("."))
        key = payload["key"]
        value = payload["value"]
        index = payload.get("index")

        store = _get_passthrough_store()
        await store.aput(namespace, key, value, index=index)

        return {"success": True}

    async def store_search(payload: dict):
        """Search stores"""
        namespace_prefix = tuple(payload["namespace_prefix"])
        filter = payload.get("filter")
        limit = payload.get("limit", 10)
        offset = payload.get("offset", 0)
        query = payload.get("query")

        store = _get_passthrough_store()
        result = await store.asearch(
            namespace_prefix, filter=filter, limit=limit, offset=offset, query=query
        )

        return [item.dict() for item in result]

    async def store_delete(payload: dict):
        """Delete store data"""

        namespace = tuple(payload["namespace"])
        key = payload["key"]

        store = _get_passthrough_store()
        await store.adelete(namespace, key)

        return {"success": True}

    async def store_list_namespaces(payload: dict):
        """List all namespaces"""
        prefix = tuple(payload.get("prefix", [])) or None
        suffix = tuple(payload.get("suffix", [])) or None
        max_depth = payload.get("max_depth")
        limit = payload.get("limit", 100)
        offset = payload.get("offset", 0)

        store = _get_passthrough_store()
        result = await store.alist_namespaces(
            prefix=prefix,
            suffix=suffix,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
        )

        return [list(ns) for ns in result]

    def wrap_handler(cb):
        async def wrapped(request: Request):
            try:
                payload = orjson.loads(await request.body())
                return ApiResponse(await cb(payload))
            except ValueError as exc:
                return ApiResponse({"error": str(exc)}, status_code=400)

        return wrapped

    remote = Starlette(
        routes=[
            Route(
                "/checkpointer_get_tuple",
                wrap_handler(checkpointer_get_tuple),
                methods=["POST"],
            ),
            Route(
                "/checkpointer_list", wrap_handler(checkpointer_list), methods=["POST"]
            ),
            Route(
                "/checkpointer_put", wrap_handler(checkpointer_put), methods=["POST"]
            ),
            Route(
                "/checkpointer_put_writes",
                wrap_handler(checkpointer_put_writes),
                methods=["POST"],
            ),
            Route("/store_get", wrap_handler(store_get), methods=["POST"]),
            Route("/store_put", wrap_handler(store_put), methods=["POST"]),
            Route("/store_delete", wrap_handler(store_delete), methods=["POST"]),
            Route("/store_search", wrap_handler(store_search), methods=["POST"]),
            Route(
                "/store_list_namespaces",
                wrap_handler(store_list_namespaces),
                methods=["POST"],
            ),
            Route("/store_batch", wrap_handler(store_batch), methods=["POST"]),
            Route("/ok", lambda _: ApiResponse({"ok": True}), methods=["GET"]),
        ]
    )

    server = uvicorn.Server(
        uvicorn.Config(
            remote,
            port=REMOTE_PORT,
            # We need to _explicitly_ set these values in order
            # to avoid reinitialising the logger, which removes
            # the structlog logger setup before.
            # See: https://github.com/encode/uvicorn/blob/8f4c8a7f34914c16650ebd026127b96560425fde/uvicorn/config.py#L357-L393
            log_config=None,
            log_level=None,
            access_log=True,
        )
    )
    await server.serve()


class DisableHttpxLoggingContextManager(AbstractContextManager):
    """
    Disable HTTP/1.1 200 OK logs spamming stdout.
    """

    filter: logging.Filter

    def filter(self, record: logging.LogRecord) -> bool:
        return "200 OK" not in record.getMessage()

    def __enter__(self):
        logging.getLogger("httpx").addFilter(self.filter)

    def __exit__(self, exc_type, exc_value, traceback):
        logging.getLogger("httpx").removeFilter(self.filter)


async def wait_until_js_ready():
    with DisableHttpxLoggingContextManager():
        async with (
            httpx.AsyncClient(
                base_url=f"http://localhost:{GRAPH_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as graph_client,
            httpx.AsyncClient(
                base_url=f"http://localhost:{REMOTE_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as checkpointer_client,
        ):
            attempt = 0
            while not asyncio.current_task().cancelled():
                try:
                    res = await graph_client.get("/ok")
                    res.raise_for_status()
                    res = await checkpointer_client.get("/ok")
                    res.raise_for_status()
                    return
                except httpx.HTTPError:
                    if attempt > 240:
                        raise
                    else:
                        attempt += 1
                        await asyncio.sleep(0.5)


async def js_healthcheck():
    with DisableHttpxLoggingContextManager():
        async with (
            httpx.AsyncClient(
                base_url=f"http://localhost:{GRAPH_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as graph_client,
            httpx.AsyncClient(
                base_url=f"http://localhost:{REMOTE_PORT}",
                limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(verify=SSL),
            ) as checkpointer_client,
        ):
            try:
                res = await graph_client.get("/ok")
                res.raise_for_status()
                res = await checkpointer_client.get("/ok")
                res.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                logger.warning(
                    "JS healthcheck failed. Either the JS server is not running or the event loop is blocked by a CPU-intensive task.",
                    error=exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="JS healthcheck failed. Either the JS server is not running or the event loop is blocked by a CPU-intensive task.",
                ) from exc
