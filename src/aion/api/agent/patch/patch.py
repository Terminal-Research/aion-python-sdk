import logging

logger = logging.getLogger(__name__)

logger.info("Patching langgraph_api")

# LangGraph Metadata Collection Note:
# The langgraph_api package includes a telemetry system that periodically sends usage 
# statistics and operational data to LangGraph's servers via the METADATA_ENDPOINT.
#
# The metadata_loop() function in langgraph_api/metadata.py runs as an async background task 
# and collects the following information:
#   - License and API keys (if configured)
#   - Usage metrics: number of graph runs and node executions
#   - Environment details: LangGraph version, platform variant, host info
#   - Configuration flags: whether indexing or custom apps are being used
#   - Timestamp ranges for the reporting period
#   - System logs collected during operation
#
# This data collection helps the LangGraph team monitor usage patterns, improve the product,
# and potentially enforce licensing requirements. 

# Import and override langgraph_api metadata collection
from langgraph_api import metadata
from langgraph_api.metadata import METADATA_ENDPOINT

# Override the endpoint to prevent data from being sent
METADATA_ENDPOINT = "" # Never send metadata to Lang servers. We may support this later.

# Override the metadata_loop function to completely disable telemetry collection
async def disabled_metadata_loop() -> None:
    """Disabled implementation of metadata_loop that does nothing."""
    return None

# Replace the original function with our disabled version
metadata.metadata_loop = disabled_metadata_loop

# =================================================

# Import the original function directly
from langgraph_api.stream import astream_state as original_astream_state
from langgraph_api.stream import AnyStream
from langgraph_api.stream import _preprocess_debug_checkpoint


# Also import all the dependencies needed for our replacement function
from langgraph_api.js.base import BaseRemotePregel
from langgraph_api.asyncio import ValueEvent, wait_if_not_done
from langchain_core.runnables.config import run_in_executor
from contextlib import AsyncExitStack, aclosing
from langgraph_api.utils import AsyncConnectionProto
from langgraph_storage.checkpoint import Checkpointer
from langgraph_storage.store import Store
from langchain_core.messages import (
    BaseMessage,
    BaseMessageChunk,
    message_chunk_to_message,
)
import langgraph.version
from langgraph.pregel.debug import CheckpointPayload, TaskResultPayload
from langgraph_api.metadata import HOST, PLAN, incr_nodes
from langgraph_api.command import map_cmd
from langgraph_api.graph import get_graph
from langgraph_api.schema import Run, StreamMode

from typing import Any, cast
from collections.abc import Callable

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
    logger.info("Logging loop x121")
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
                logger.info("Logging loop x123")
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
        logger.info("Logging loop x125")
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
                logger.info("logger x125event %s", event)
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

# Replace the original function with our patched version
import langgraph_api.stream
langgraph_api.stream.astream_state = astream_state


# @todo 
# config.FF_CRONS_ENABLED
# config.N_JOBS_PER_WORKER
# config.STORE_CONFIG
