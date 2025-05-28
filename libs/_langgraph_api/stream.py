from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, aclosing
from functools import lru_cache
from typing import Any, cast

import langgraph.version
import langsmith
import structlog
from langchain_core.messages import (
    BaseMessage,
    BaseMessageChunk,
    message_chunk_to_message,
)
from langchain_core.runnables.config import run_in_executor
from langgraph.errors import (
    EmptyChannelError,
    EmptyInputError,
    GraphRecursionError,
    InvalidUpdateError,
)
from langgraph.pregel.debug import CheckpointPayload, TaskResultPayload
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorLegacy

from langgraph_api.asyncio import ValueEvent, wait_if_not_done
from langgraph_api.command import map_cmd
from langgraph_api.graph import get_graph
from langgraph_api.js.base import BaseRemotePregel
from langgraph_api.metadata import HOST, PLAN, incr_nodes
from langgraph_api.schema import Run, StreamMode
from langgraph_api.serde import json_dumpb
from langgraph_api.utils import AsyncConnectionProto
from langgraph_storage.checkpoint import Checkpointer
from langgraph_storage.ops import Runs
from langgraph_storage.store import Store

logger = structlog.stdlib.get_logger(__name__)

AnyStream = AsyncIterator[tuple[str, Any]]


def _preproces_debug_checkpoint_task(task: dict[str, Any]) -> dict[str, Any]:
    if (
        "state" not in task
        or not task["state"]
        or "configurable" not in task["state"]
        or not task["state"]["configurable"]
    ):
        return task

    task["checkpoint"] = task["state"]["configurable"]
    del task["state"]
    return task


def _preprocess_debug_checkpoint(payload: CheckpointPayload | None) -> dict[str, Any]:
    from langgraph_api.state import runnable_config_to_checkpoint

    if not payload:
        return None

    payload["checkpoint"] = runnable_config_to_checkpoint(payload["config"])
    payload["parent_checkpoint"] = runnable_config_to_checkpoint(
        payload["parent_config"] if "parent_config" in payload else None
    )

    payload["tasks"] = [_preproces_debug_checkpoint_task(t) for t in payload["tasks"]]

    # TODO: deprecate the `config`` and `parent_config`` fields
    return payload


async def astream_state(
    stack: AsyncExitStack,
    conn: AsyncConnectionProto,
    run: Run,
    attempt: int,
    done: ValueEvent,
    *,
    on_checkpoint: Callable[[CheckpointPayload], None] = lambda _: None,
    on_task_result: Callable[[TaskResultPayload], None] = lambda _: None,
) -> AnyStream:
    """Stream messages from the runnable."""
    run_id = str(run["run_id"])
    await stack.enter_async_context(conn.pipeline())
    # extract args from run
    kwargs = run["kwargs"].copy()
    kwargs.pop("webhook", None)
    subgraphs = kwargs.get("subgraphs", False)
    temporary = kwargs.pop("temporary", False)
    config = kwargs.pop("config")
    graph = await stack.enter_async_context(
        get_graph(
            config["configurable"]["graph_id"],
            config,
            store=Store(),
            checkpointer=None if temporary else Checkpointer(conn),
        )
    )
    input = kwargs.pop("input")
    if cmd := kwargs.pop("command"):
        input = map_cmd(cmd)
    stream_mode: list[StreamMode] = kwargs.pop("stream_mode")
    feedback_keys = kwargs.pop("feedback_keys", None)
    stream_modes_set: set[StreamMode] = set(stream_mode) - {"events"}
    if "debug" not in stream_modes_set:
        stream_modes_set.add("debug")
    if "messages-tuple" in stream_modes_set and not isinstance(graph, BaseRemotePregel):
        stream_modes_set.remove("messages-tuple")
        stream_modes_set.add("messages")
    # attach attempt metadata
    config["metadata"]["run_attempt"] = attempt
    # attach langgraph metadata
    config["metadata"]["langgraph_version"] = langgraph.version.__version__
    config["metadata"]["langgraph_plan"] = PLAN
    config["metadata"]["langgraph_host"] = HOST
    # attach node counter
    if not isinstance(graph, BaseRemotePregel):
        config["configurable"]["__pregel_node_finished"] = incr_nodes
        # TODO add node tracking for JS graphs
    # attach run_id to config
    # for attempts beyond the first, use a fresh, unique run_id
    config = {**config, "run_id": run["run_id"]} if attempt == 1 else config
    # set up state
    checkpoint: CheckpointPayload | None = None
    messages: dict[str, BaseMessageChunk] = {}
    use_astream_events = "events" in stream_mode or isinstance(graph, BaseRemotePregel)
    # yield metadata chunk
    yield "metadata", {"run_id": run_id, "attempt": attempt}
    # stream run
    if use_astream_events:
        async with (
            stack,
            aclosing(
                graph.astream_events(
                    input,
                    config,
                    version="v2",
                    stream_mode=list(stream_modes_set),
                    **kwargs,
                )
            ) as stream,
        ):
            sentinel = object()
            while True:
                event = await wait_if_not_done(anext(stream, sentinel), done)
                if event is sentinel:
                    break
                if event.get("tags") and "langsmith:hidden" in event["tags"]:
                    continue
                if "messages" in stream_mode and isinstance(graph, BaseRemotePregel):
                    if event["event"] == "on_custom_event" and event["name"] in (
                        "messages/complete",
                        "messages/partial",
                        "messages/metadata",
                    ):
                        yield event["name"], event["data"]
                # TODO support messages-tuple for js graphs
                if event["event"] == "on_chain_stream" and event["run_id"] == run_id:
                    if subgraphs:
                        ns, mode, chunk = event["data"]["chunk"]
                    else:
                        mode, chunk = event["data"]["chunk"]
                    # --- begin shared logic with astream ---
                    if mode == "debug":
                        if chunk["type"] == "checkpoint":
                            checkpoint = _preprocess_debug_checkpoint(chunk["payload"])
                            on_checkpoint(checkpoint)
                        elif chunk["type"] == "task_result":
                            on_task_result(chunk["payload"])
                    if mode == "messages":
                        if "messages-tuple" in stream_mode:
                            yield "messages", chunk
                        else:
                            msg, meta = cast(tuple[BaseMessage, dict[str, Any]], chunk)
                            if msg.id in messages:
                                messages[msg.id] += msg
                            else:
                                messages[msg.id] = msg
                                yield "messages/metadata", {msg.id: {"metadata": meta}}
                            yield (
                                (
                                    "messages/partial"
                                    if isinstance(msg, BaseMessageChunk)
                                    else "messages/complete"
                                ),
                                [message_chunk_to_message(messages[msg.id])],
                            )
                    elif mode in stream_mode:
                        if subgraphs and ns:
                            yield f"{mode}|{'|'.join(ns)}", chunk
                        else:
                            yield mode, chunk
                    # --- end shared logic with astream ---
                elif "events" in stream_mode:
                    yield "events", event
    else:
        async with (
            stack,
            aclosing(
                graph.astream(
                    input, config, stream_mode=list(stream_modes_set), **kwargs
                )
            ) as stream,
        ):
            sentinel = object()
            while True:
                event = await wait_if_not_done(anext(stream, sentinel), done)
                if event is sentinel:
                    break
                if subgraphs:
                    ns, mode, chunk = event
                else:
                    mode, chunk = event
                # --- begin shared logic with astream_events ---
                if mode == "debug":
                    if chunk["type"] == "checkpoint":
                        checkpoint = _preprocess_debug_checkpoint(chunk["payload"])
                        on_checkpoint(checkpoint)
                    elif chunk["type"] == "task_result":
                        on_task_result(chunk["payload"])
                if mode == "messages":
                    if "messages-tuple" in stream_mode:
                        yield "messages", chunk
                    else:
                        msg, meta = cast(tuple[BaseMessage, dict[str, Any]], chunk)
                        if msg.id in messages:
                            messages[msg.id] += msg
                        else:
                            messages[msg.id] = msg
                            yield "messages/metadata", {msg.id: {"metadata": meta}}
                        yield (
                            (
                                "messages/partial"
                                if isinstance(msg, BaseMessageChunk)
                                else "messages/complete"
                            ),
                            [message_chunk_to_message(messages[msg.id])],
                        )
                elif mode in stream_mode:
                    if subgraphs and ns:
                        yield f"{mode}|{'|'.join(ns)}", chunk
                    else:
                        yield mode, chunk
                # --- end shared logic with astream_events ---
    # Get feedback URLs
    if feedback_keys:
        feedback_urls = await run_in_executor(
            None, get_feedback_urls, run_id, feedback_keys
        )
        yield "feedback", feedback_urls


async def consume(stream: AnyStream, run_id: str) -> None:
    async with aclosing(stream):
        try:
            async for mode, payload in stream:
                await Runs.Stream.publish(
                    run_id, mode, await run_in_executor(None, json_dumpb, payload)
                )
        except Exception as e:
            if isinstance(e, ExceptionGroup):
                e = e.exceptions[0]
            await Runs.Stream.publish(
                run_id, "error", await run_in_executor(None, json_dumpb, e)
            )
            raise e from None


def get_feedback_urls(run_id: str, feedback_keys: list[str]) -> dict[str, str]:
    client = get_langsmith_client()
    tokens = client.create_presigned_feedback_tokens(run_id, feedback_keys)
    return {key: token.url for key, token in zip(feedback_keys, tokens, strict=False)}


@lru_cache(maxsize=1)
def get_langsmith_client() -> langsmith.Client:
    return langsmith.Client()


EXPECTED_ERRORS = (
    ValueError,
    InvalidUpdateError,
    GraphRecursionError,
    EmptyInputError,
    EmptyChannelError,
    ValidationError,
    ValidationErrorLegacy,
)
