import asyncio
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, TypedDict
from uuid import UUID

from langgraph.checkpoint.base.id import uuid6
from starlette.authentication import BaseUser
from starlette.exceptions import HTTPException

from langgraph_api.graph import GRAPHS, get_assistant_id
from langgraph_api.schema import (
    All,
    Config,
    IfNotExists,
    MetadataInput,
    MultitaskStrategy,
    OnCompletion,
    Run,
    RunCommand,
    StreamMode,
)
from langgraph_api.utils import AsyncConnectionProto, get_auth_ctx
from langgraph_storage.ops import Runs, logger


class RunCreateDict(TypedDict):
    """Payload for creating a run."""

    assistant_id: str
    """Assistant ID to use for this run."""
    checkpoint_id: str | None
    """Checkpoint ID to start from. Defaults to the latest checkpoint."""
    input: Sequence[dict] | dict[str, Any] | None
    """Input to the run. Pass null to resume from the current state of the thread."""
    command: RunCommand | None
    """One or more commands to update the graph's state and send messages to nodes."""
    metadata: MetadataInput
    """Metadata for the run."""
    config: Config | None
    """Additional configuration for the run."""
    webhook: str | None
    """Webhook to call when the run is complete."""

    interrupt_before: All | list[str] | None
    """Interrupt execution before entering these nodes."""
    interrupt_after: All | list[str] | None
    """Interrupt execution after leaving these nodes."""

    multitask_strategy: MultitaskStrategy
    """Strategy to handle concurrent runs on the same thread. Only relevant if
    there is a pending/inflight run on the same thread. One of:
    - "reject": Reject the new run.
    - "interrupt": Interrupt the current run, keeping steps completed until now,
       and start a new one.
    - "rollback": Cancel and delete the existing run, rolling back the thread to
      the state before it had started, then start the new run.
    - "enqueue": Queue up the new run to start after the current run finishes.
    """
    on_completion: OnCompletion
    """What to do when the run completes. One of:
    - "keep": Keep the thread in the database.
    - "delete": Delete the thread from the database.
    """
    stream_mode: list[StreamMode] | StreamMode
    """One or more of "values", "messages", "updates" or "events".
    - "values": Stream the thread state any time it changes.
    - "messages": Stream chat messages from thread state and calls to chat models, 
      token-by-token where possible.
    - "updates": Stream the state updates returned by each node.
    - "events": Stream all events produced by sub-runs (eg. nodes, LLMs, etc.).
    - "custom": Stream custom events produced by your nodes.
    """
    stream_subgraphs: bool | None
    """Stream output from subgraphs. By default, streams only the top graph."""
    feedback_keys: list[str] | None
    """Pass one or more feedback_keys if you want to request short-lived signed URLs
    for submitting feedback to LangSmith with this key for this run."""
    after_seconds: int | None
    """Start the run after this many seconds. Defaults to 0."""
    if_not_exists: IfNotExists
    """Create the thread if it doesn't exist. If False, reply with 404."""


def ensure_ids(
    assistant_id: str | UUID,
    thread_id: str | UUID | None,
    payload: RunCreateDict,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]:
    try:
        results = [
            assistant_id if isinstance(assistant_id, UUID) else UUID(assistant_id)
        ]
    except ValueError:
        keys = ", ".join(GRAPHS.keys())
        raise HTTPException(
            status_code=422,
            detail=f"Invalid assistant: '{assistant_id}'. Must be either:\n"
            f"- A valid assistant UUID, or\n"
            f"- One of the registered graphs: {keys}",
        ) from None
    if thread_id:
        try:
            results.append(
                thread_id if isinstance(thread_id, UUID) else UUID(thread_id)
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid thread ID") from None
    else:
        results.append(None)
    if checkpoint_id := payload.get("checkpoint_id"):
        try:
            results.append(
                checkpoint_id
                if isinstance(checkpoint_id, UUID)
                else UUID(checkpoint_id)
            )
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Invalid checkpoint ID"
            ) from None
    else:
        results.append(None)
    return tuple(results)


def assign_defaults(
    payload: RunCreateDict,
):
    if payload.get("stream_mode"):
        stream_mode = (
            payload["stream_mode"]
            if isinstance(payload["stream_mode"], list)
            else [payload["stream_mode"]]
        )
    else:
        stream_mode = ["values"]
    multitask_strategy = payload.get("multitask_strategy") or "reject"
    prevent_insert_if_inflight = multitask_strategy == "reject"
    return stream_mode, multitask_strategy, prevent_insert_if_inflight


def get_user_id(user: BaseUser | None) -> str | None:
    if user is None:
        return None
    try:
        return user.identity
    except NotImplementedError:
        try:
            return user.display_name
        except NotImplementedError:
            pass


async def create_valid_run(
    conn: AsyncConnectionProto,
    thread_id: str | None,
    payload: RunCreateDict,
    headers: Mapping[str, str],
    barrier: asyncio.Barrier | None = None,
    run_id: UUID | None = None,
) -> Run:
    (
        assistant_id,
        thread_id,
        checkpoint_id,
        run_id,
    ) = _get_ids(
        thread_id,
        payload,
        run_id=run_id,
    )
    temporary = thread_id is None and payload.get("on_completion", "delete") == "delete"
    stream_mode, multitask_strategy, prevent_insert_if_inflight = assign_defaults(
        payload
    )

    # assign custom headers and checkpoint to config
    config = payload.get("config") or {}
    config.setdefault("configurable", {})
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = str(checkpoint_id)
    if checkpoint := payload.get("checkpoint"):
        config["configurable"].update(checkpoint)
    for key, value in headers.items():
        if key.startswith("x-"):
            if key in (
                "x-api-key",
                "x-tenant-id",
                "x-service-key",
            ):
                continue
            config["configurable"][key] = value
    ctx = get_auth_ctx()
    if ctx:
        user = ctx.user
        user_id = get_user_id(user)
        config["configurable"]["langgraph_auth_user"] = user
        config["configurable"]["langgraph_auth_user_id"] = user_id
        config["configurable"]["langgraph_auth_permissions"] = ctx.permissions
    else:
        user_id = None
    run_coro = Runs.put(
        conn,
        assistant_id,
        {
            "input": payload.get("input"),
            "command": payload.get("command"),
            "config": config,
            "stream_mode": stream_mode,
            "interrupt_before": payload.get("interrupt_before"),
            "interrupt_after": payload.get("interrupt_after"),
            "webhook": payload.get("webhook"),
            "feedback_keys": payload.get("feedback_keys"),
            "temporary": temporary,
            "subgraphs": payload.get("stream_subgraphs", False),
        },
        metadata=payload.get("metadata"),
        status="pending",
        user_id=user_id,
        thread_id=thread_id,
        run_id=run_id,
        multitask_strategy=multitask_strategy,
        prevent_insert_if_inflight=prevent_insert_if_inflight,
        after_seconds=payload.get("after_seconds", 0),
        if_not_exists=payload.get("if_not_exists", "reject"),
    )
    run_ = await run_coro

    if barrier:
        await barrier.wait()

    # abort if thread, assistant, etc not found
    try:
        first = await anext(run_)
    except StopAsyncIteration:
        raise HTTPException(
            status_code=404, detail="Thread or assistant not found."
        ) from None

    # handle multitask strategy
    inflight_runs = [run async for run in run_]
    if first["run_id"] == run_id:
        logger.info("Created run", run_id=str(run_id), thread_id=str(thread_id))
        # inserted, proceed
        if multitask_strategy in ("interrupt", "rollback") and inflight_runs:
            try:
                await Runs.cancel(
                    conn,
                    [run["run_id"] for run in inflight_runs],
                    thread_id=thread_id,
                    action=multitask_strategy,
                )
            except HTTPException:
                # if we can't find the inflight runs again, we can proceeed
                pass
        return first
    elif multitask_strategy == "reject":
        raise HTTPException(
            status_code=409,
            detail="Thread is already running a task. Wait for it to finish or choose a different multitask strategy.",
        )
    else:
        raise NotImplementedError


class _Ids(NamedTuple):
    assistant_id: uuid.UUID
    thread_id: uuid.UUID | None
    checkpoint_id: uuid.UUID | None
    run_id: uuid.UUID


def _get_ids(
    thread_id: str | None,
    payload: RunCreateDict,
    run_id: UUID | None = None,
) -> _Ids:
    # get assistant_id
    assistant_id = get_assistant_id(payload["assistant_id"])

    # ensure UUID validity defaults
    assistant_id, thread_id, checkpoint_id = ensure_ids(
        assistant_id, thread_id, payload
    )

    run_id = run_id or uuid6()

    return _Ids(
        assistant_id,
        thread_id,
        checkpoint_id,
        run_id,
    )
