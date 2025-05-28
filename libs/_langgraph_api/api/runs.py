import asyncio
from collections.abc import AsyncIterator

import orjson
from langgraph.checkpoint.base.id import uuid6
from starlette.responses import Response, StreamingResponse

from langgraph_api import config
from langgraph_api.asyncio import ValueEvent, aclosing
from langgraph_api.models.run import create_valid_run
from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute
from langgraph_api.sse import EventSourceResponse
from langgraph_api.utils import fetchone, validate_uuid
from langgraph_api.validation import (
    CronCreate,
    CronSearch,
    RunBatchCreate,
    RunCreateStateful,
    RunCreateStateless,
)
from langgraph_license.validation import plus_features_enabled
from langgraph_storage.database import connect
from langgraph_storage.ops import Crons, Runs, Threads
from langgraph_storage.retry import retry_db


@retry_db
async def create_run(request: ApiRequest):
    """Create a run."""
    thread_id = request.path_params["thread_id"]
    payload = await request.json(RunCreateStateful)
    async with connect() as conn:
        run = await create_valid_run(
            conn,
            thread_id,
            payload,
            request.headers,
        )
    return ApiResponse(run)


@retry_db
async def create_stateless_run(request: ApiRequest):
    """Create a run."""
    payload = await request.json(RunCreateStateless)
    async with connect() as conn:
        run = await create_valid_run(
            conn,
            None,
            payload,
            request.headers,
        )
    return ApiResponse(run)


async def create_stateless_run_batch(request: ApiRequest):
    """Create a batch of stateless backround runs."""
    batch_payload = await request.json(RunBatchCreate)
    async with connect() as conn, conn.pipeline():
        # barrier so all queries are sent before fetching any results
        barrier = asyncio.Barrier(len(batch_payload))
        coros = [
            create_valid_run(
                conn,
                None,
                payload,
                request.headers,
                barrier,
            )
            for payload in batch_payload
        ]
        runs = await asyncio.gather(*coros)
    return ApiResponse(runs)


async def stream_run(
    request: ApiRequest,
):
    """Create a run."""
    thread_id = request.path_params["thread_id"]
    payload = await request.json(RunCreateStateful)
    on_disconnect = payload.get("on_disconnect", "continue")
    run_id = uuid6()
    sub = asyncio.create_task(Runs.Stream.subscribe(run_id))

    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                thread_id,
                payload,
                request.headers,
                run_id=run_id,
            )
    except Exception:
        if not sub.cancelled():
            handle = await sub
            await handle.__aexit__(None, None, None)
        raise

    return EventSourceResponse(
        Runs.Stream.join(
            run["run_id"],
            thread_id=thread_id,
            cancel_on_disconnect=on_disconnect == "cancel",
            stream_mode=await sub,
        ),
        headers={"Location": f"/threads/{thread_id}/runs/{run['run_id']}/stream"},
    )


async def stream_run_stateless(
    request: ApiRequest,
):
    """Create a stateless run."""
    payload = await request.json(RunCreateStateless)
    on_disconnect = payload.get("on_disconnect", "continue")
    run_id = uuid6()
    sub = asyncio.create_task(Runs.Stream.subscribe(run_id))

    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                None,
                payload,
                request.headers,
                run_id=run_id,
            )
    except Exception:
        if not sub.cancelled():
            handle = await sub
            await handle.__aexit__(None, None, None)
        raise

    return EventSourceResponse(
        Runs.Stream.join(
            run["run_id"],
            thread_id=run["thread_id"],
            ignore_404=True,
            cancel_on_disconnect=on_disconnect == "cancel",
            stream_mode=await sub,
        ),
        headers={
            "Location": f"/threads/{run['thread_id']}/runs/{run['run_id']}/stream"
        },
    )


@retry_db
async def wait_run(request: ApiRequest):
    """Create a run, wait for the output."""
    thread_id = request.path_params["thread_id"]
    payload = await request.json(RunCreateStateful)
    run_id = uuid6()
    sub = asyncio.create_task(Runs.Stream.subscribe(run_id))

    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                thread_id,
                payload,
                request.headers,
                run_id=run_id,
            )
    except Exception:
        if not sub.cancelled():
            handle = await sub
            await handle.__aexit__(None, None, None)
        raise

    last_chunk = ValueEvent()

    async def consume():
        vchunk: bytes | None = None
        async with aclosing(
            Runs.Stream.join(
                run["run_id"], thread_id=run["thread_id"], stream_mode=await sub
            )
        ) as stream:
            async for mode, chunk in stream:
                if mode == b"values":
                    vchunk = chunk
                elif mode == b"error":
                    vchunk = orjson.dumps({"__error__": orjson.Fragment(chunk)})
        if vchunk is not None:
            last_chunk.set(vchunk)
        else:
            async with connect() as conn:
                thread_iter = await Threads.get(conn, thread_id)
                try:
                    thread = await anext(thread_iter)
                    last_chunk.set(thread["values"])
                except StopAsyncIteration:
                    last_chunk.set(b"{}")

    # keep the connection open by sending whitespace every 5 seconds
    # leading whitespace will be ignored by json parsers
    async def body() -> AsyncIterator[bytes]:
        stream = asyncio.create_task(consume())
        while True:
            try:
                yield await asyncio.wait_for(last_chunk.wait(), timeout=5)
                break
            except TimeoutError:
                yield b"\n"
            except asyncio.CancelledError:
                stream.cancel()
                await stream
                raise

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers={"Location": f"/threads/{thread_id}/runs/{run['run_id']}/join"},
    )


@retry_db
async def wait_run_stateless(request: ApiRequest):
    """Create a stateless run, wait for the output."""
    payload = await request.json(RunCreateStateless)
    run_id = uuid6()
    sub = asyncio.create_task(Runs.Stream.subscribe(run_id))

    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                None,
                payload,
                request.headers,
                run_id=run_id,
            )
    except Exception:
        if not sub.cancelled():
            handle = await sub
            await handle.__aexit__(None, None, None)
        raise

    last_chunk = ValueEvent()

    async def consume():
        vchunk: bytes | None = None
        async with aclosing(
            Runs.Stream.join(
                run["run_id"], thread_id=run["thread_id"], stream_mode=await sub
            )
        ) as stream:
            async for mode, chunk in stream:
                if mode == b"values":
                    vchunk = chunk
                elif mode == b"error":
                    vchunk = orjson.dumps({"__error__": orjson.Fragment(chunk)})
        if vchunk is not None:
            last_chunk.set(vchunk)
        else:
            # we can't fetch the thread (it was deleted), so just return empty values
            last_chunk.set(b"{}")

    # keep the connection open by sending whitespace every 5 seconds
    # leading whitespace will be ignored by json parsers
    async def body() -> AsyncIterator[bytes]:
        stream = asyncio.create_task(consume())
        while True:
            try:
                yield await asyncio.wait_for(last_chunk.wait(), timeout=5)
                break
            except TimeoutError:
                yield b"\n"
            except asyncio.CancelledError:
                stream.cancel()
                await stream
                raise

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers={"Location": f"/threads/{run['thread_id']}/runs/{run['run_id']}/join"},
    )


@retry_db
async def list_runs_http(
    request: ApiRequest,
):
    """List all runs for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))
    status = request.query_params.get("status")

    async with connect() as conn, conn.pipeline():
        thread, runs = await asyncio.gather(
            Threads.get(conn, thread_id),
            Runs.search(
                conn,
                thread_id,
                limit=limit,
                offset=offset,
                status=status,
                metadata=None,
            ),
        )
    await fetchone(thread)
    return ApiResponse([run async for run in runs])


@retry_db
async def get_run_http(request: ApiRequest):
    """Get a run by ID."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    async with connect() as conn, conn.pipeline():
        thread, run = await asyncio.gather(
            Threads.get(conn, thread_id),
            Runs.get(
                conn,
                run_id,
                thread_id=thread_id,
            ),
        )
    await fetchone(thread)
    return ApiResponse(await fetchone(run))


@retry_db
async def join_run(request: ApiRequest):
    """Wait for a run to finish."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    return ApiResponse(
        await Runs.join(
            run_id,
            thread_id=thread_id,
        )
    )


@retry_db
async def join_run_stream_endpoint(request: ApiRequest):
    """Wait for a run to finish."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    cancel_on_disconnect_str = request.query_params.get("cancel_on_disconnect", "false")
    cancel_on_disconnect = cancel_on_disconnect_str.lower() in {"true", "yes", "1"}
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")
    stream_mode = request.query_params.get("stream_mode") or None
    return EventSourceResponse(
        Runs.Stream.join(
            run_id,
            thread_id=thread_id,
            cancel_on_disconnect=cancel_on_disconnect,
            stream_mode=stream_mode,
        )
    )


@retry_db
async def cancel_run(
    request: ApiRequest,
):
    """Cancel a run."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")
    wait_str = request.query_params.get("wait", "false")
    wait = wait_str.lower() in {"true", "yes", "1"}
    action_str = request.query_params.get("action", "interrupt")
    action = action_str if action_str in {"interrupt", "rollback"} else "interrupt"

    async with connect() as conn:
        await Runs.cancel(
            conn,
            [run_id],
            action=action,
            thread_id=thread_id,
        )
    if wait:
        await Runs.join(
            run_id,
            thread_id=thread_id,
        )
    return Response(status_code=204 if wait else 202)


@retry_db
async def delete_run(request: ApiRequest):
    """Delete a run by ID."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    async with connect() as conn:
        rid = await Runs.delete(
            conn,
            run_id,
            thread_id=thread_id,
        )
    await fetchone(rid)
    return Response(status_code=204)


@retry_db
async def create_cron(request: ApiRequest):
    """Create a cron with new thread."""
    payload = await request.json(CronCreate)

    async with connect() as conn:
        cron = await Crons.put(
            conn,
            thread_id=None,
            end_time=payload.get("end_time"),
            schedule=payload.get("schedule"),
            payload=payload,
        )
    return ApiResponse(await fetchone(cron))


@retry_db
async def create_thread_cron(request: ApiRequest):
    """Create a thread specific cron."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    payload = await request.json(CronCreate)

    async with connect() as conn:
        cron = await Crons.put(
            conn,
            thread_id=thread_id,
            end_time=payload.get("end_time"),
            schedule=payload.get("schedule"),
            payload=payload,
        )
    return ApiResponse(await fetchone(cron))


@retry_db
async def delete_cron(request: ApiRequest):
    """Delete a cron by ID."""
    cron_id = request.path_params["cron_id"]
    validate_uuid(cron_id, "Invalid cron ID: must be a UUID")

    async with connect() as conn:
        cid = await Crons.delete(
            conn,
            cron_id=cron_id,
        )
    await fetchone(cid)
    return Response(status_code=204)


@retry_db
async def search_crons(request: ApiRequest):
    """List all cron jobs for an assistant"""
    payload = await request.json(CronSearch)
    if assistant_id := payload.get("assistant_id"):
        validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    if thread_id := payload.get("thread_id"):
        validate_uuid(thread_id, "Invalid thread ID: must be a UUID")

    async with connect() as conn:
        crons_iter = await Crons.search(
            conn,
            assistant_id=assistant_id,
            thread_id=thread_id,
            limit=int(payload.get("limit", 10)),
            offset=int(payload.get("offset", 0)),
        )
    return ApiResponse([cron async for cron in crons_iter])


runs_routes = [
    ApiRoute("/runs/stream", stream_run_stateless, methods=["POST"]),
    ApiRoute("/runs/wait", wait_run_stateless, methods=["POST"]),
    ApiRoute("/runs", create_stateless_run, methods=["POST"]),
    ApiRoute("/runs/batch", create_stateless_run_batch, methods=["POST"]),
    (
        ApiRoute("/runs/crons", create_cron, methods=["POST"])
        if config.FF_CRONS_ENABLED and plus_features_enabled()
        else None
    ),
    (
        ApiRoute("/runs/crons/search", search_crons, methods=["POST"])
        if config.FF_CRONS_ENABLED and plus_features_enabled()
        else None
    ),
    ApiRoute("/threads/{thread_id}/runs/{run_id}/join", join_run, methods=["GET"]),
    ApiRoute(
        "/threads/{thread_id}/runs/{run_id}/stream",
        join_run_stream_endpoint,
        methods=["GET"],
    ),
    ApiRoute("/threads/{thread_id}/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs/{run_id}", get_run_http, methods=["GET"]),
    ApiRoute("/threads/{thread_id}/runs/{run_id}", delete_run, methods=["DELETE"]),
    ApiRoute("/threads/{thread_id}/runs/stream", stream_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs/wait", wait_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs", create_run, methods=["POST"]),
    (
        ApiRoute(
            "/threads/{thread_id}/runs/crons", create_thread_cron, methods=["POST"]
        )
        if config.FF_CRONS_ENABLED and plus_features_enabled()
        else None
    ),
    ApiRoute("/threads/{thread_id}/runs", list_runs_http, methods=["GET"]),
    (
        ApiRoute("/runs/crons/{cron_id}", delete_cron, methods=["DELETE"])
        if config.FF_CRONS_ENABLED and plus_features_enabled()
        else None
    ),
]

runs_routes = [route for route in runs_routes if route is not None]
