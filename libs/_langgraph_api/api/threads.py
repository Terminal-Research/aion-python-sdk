from uuid import uuid4

from starlette.responses import Response
from starlette.routing import BaseRoute

from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute
from langgraph_api.state import state_snapshot_to_thread_state
from langgraph_api.utils import fetchone, validate_uuid
from langgraph_api.validation import (
    ThreadCreate,
    ThreadPatch,
    ThreadSearchRequest,
    ThreadStateCheckpointRequest,
    ThreadStateSearch,
    ThreadStateUpdate,
)
from langgraph_storage.database import connect
from langgraph_storage.ops import Threads
from langgraph_storage.retry import retry_db


@retry_db
async def create_thread(
    request: ApiRequest,
):
    """Create a thread."""
    payload = await request.json(ThreadCreate)
    if thread_id := payload.get("thread_id"):
        validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    async with connect() as conn:
        thread_id = thread_id or str(uuid4())
        iter = await Threads.put(
            conn,
            thread_id,
            metadata=payload.get("metadata"),
            if_exists=payload.get("if_exists") or "raise",
        )

        if supersteps := payload.get("supersteps"):
            await Threads.State.bulk(
                conn,
                config={"configurable": {"thread_id": thread_id}},
                supersteps=supersteps,
            )

    return ApiResponse(await fetchone(iter, not_found_code=409))


@retry_db
async def search_threads(
    request: ApiRequest,
):
    """List threads."""
    payload = await request.json(ThreadSearchRequest)
    async with connect() as conn:
        iter = await Threads.search(
            conn,
            status=payload.get("status"),
            values=payload.get("values"),
            metadata=payload.get("metadata"),
            limit=payload.get("limit") or 10,
            offset=payload.get("offset") or 0,
        )
    return ApiResponse([thread async for thread in iter])


@retry_db
async def get_thread_state(
    request: ApiRequest,
):
    """Get state for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    subgraphs = request.query_params.get("subgraphs") in ("true", "True")
    async with connect() as conn:
        state = state_snapshot_to_thread_state(
            await Threads.State.get(
                conn, {"configurable": {"thread_id": thread_id}}, subgraphs=subgraphs
            )
        )
    return ApiResponse(state)


@retry_db
async def get_thread_state_at_checkpoint(
    request: ApiRequest,
):
    """Get state for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    checkpoint_id = request.path_params["checkpoint_id"]
    async with connect() as conn:
        state = state_snapshot_to_thread_state(
            await Threads.State.get(
                conn,
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": checkpoint_id,
                    }
                },
                subgraphs=request.query_params.get("subgraphs") in ("true", "True"),
            )
        )
    return ApiResponse(state)


@retry_db
async def get_thread_state_at_checkpoint_post(
    request: ApiRequest,
):
    """Get state for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    payload = await request.json(ThreadStateCheckpointRequest)
    async with connect() as conn:
        state = state_snapshot_to_thread_state(
            await Threads.State.get(
                conn,
                {"configurable": {"thread_id": thread_id, **payload["checkpoint"]}},
                subgraphs=payload.get("subgraphs", False),
            )
        )
    return ApiResponse(state)


@retry_db
async def update_thread_state(
    request: ApiRequest,
):
    """Add state to a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    payload = await request.json(ThreadStateUpdate)
    config = {"configurable": {"thread_id": thread_id}}
    if payload.get("checkpoint_id"):
        config["configurable"]["checkpoint_id"] = payload["checkpoint_id"]
    if payload.get("checkpoint"):
        config["configurable"].update(payload["checkpoint"])
    try:
        if user_id := request.user.display_name:
            config["configurable"]["user_id"] = user_id
    except AssertionError:
        pass
    async with connect() as conn:
        inserted = await Threads.State.post(
            conn,
            config,
            payload.get("values"),
            payload.get("as_node"),
        )
    return ApiResponse(inserted)


@retry_db
async def get_thread_history(
    request: ApiRequest,
):
    """Get all past states for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    limit = request.query_params.get("limit", 10)
    before = request.query_params.get("before")
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    async with connect() as conn:
        states = [
            state_snapshot_to_thread_state(c)
            for c in await Threads.State.list(
                conn, config=config, limit=limit, before=before
            )
        ]
    return ApiResponse(states)


@retry_db
async def get_thread_history_post(
    request: ApiRequest,
):
    """Get all past states for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    payload = await request.json(ThreadStateSearch)
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    config["configurable"].update(payload.get("checkpoint", {}))
    async with connect() as conn:
        states = [
            state_snapshot_to_thread_state(c)
            for c in await Threads.State.list(
                conn,
                config=config,
                limit=int(payload.get("limit") or 10),
                before=payload.get("before"),
                metadata=payload.get("metadata"),
            )
        ]
    return ApiResponse(states)


@retry_db
async def get_thread(
    request: ApiRequest,
):
    """Get a thread by ID."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    async with connect() as conn:
        thread = await Threads.get(conn, thread_id)
    return ApiResponse(await fetchone(thread))


@retry_db
async def patch_thread(
    request: ApiRequest,
):
    """Update a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    payload = await request.json(ThreadPatch)
    async with connect() as conn:
        thread = await Threads.patch(conn, thread_id, metadata=payload["metadata"])
    return ApiResponse(await fetchone(thread))


@retry_db
async def delete_thread(request: ApiRequest):
    """Delete a thread by ID."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    async with connect() as conn:
        tid = await Threads.delete(conn, thread_id)
    await fetchone(tid)
    return Response(status_code=204)


@retry_db
async def copy_thread(request: ApiRequest):
    thread_id = request.path_params["thread_id"]
    async with connect() as conn:
        iter = await Threads.copy(conn, thread_id)
    return ApiResponse(await fetchone(iter, not_found_code=409))


threads_routes: list[BaseRoute] = [
    ApiRoute("/threads", endpoint=create_thread, methods=["POST"]),
    ApiRoute("/threads/search", endpoint=search_threads, methods=["POST"]),
    ApiRoute("/threads/{thread_id}", endpoint=get_thread, methods=["GET"]),
    ApiRoute("/threads/{thread_id}", endpoint=patch_thread, methods=["PATCH"]),
    ApiRoute("/threads/{thread_id}", endpoint=delete_thread, methods=["DELETE"]),
    ApiRoute("/threads/{thread_id}/state", endpoint=get_thread_state, methods=["GET"]),
    ApiRoute(
        "/threads/{thread_id}/state", endpoint=update_thread_state, methods=["POST"]
    ),
    ApiRoute(
        "/threads/{thread_id}/history", endpoint=get_thread_history, methods=["GET"]
    ),
    ApiRoute("/threads/{thread_id}/copy", endpoint=copy_thread, methods=["POST"]),
    ApiRoute(
        "/threads/{thread_id}/history",
        endpoint=get_thread_history_post,
        methods=["POST"],
    ),
    ApiRoute(
        "/threads/{thread_id}/state/{checkpoint_id}",
        endpoint=get_thread_state_at_checkpoint,
        methods=["GET"],
    ),
    ApiRoute(
        "/threads/{thread_id}/state/checkpoint",
        endpoint=get_thread_state_at_checkpoint_post,
        methods=["POST"],
    ),
]
