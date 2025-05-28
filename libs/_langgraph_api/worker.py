import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import TypedDict, cast

import structlog
from langgraph.pregel.debug import CheckpointPayload, TaskResultPayload
from starlette.exceptions import HTTPException

from langgraph_api.auth.custom import SimpleUser, normalize_user
from langgraph_api.config import (
    BG_JOB_MAX_RETRIES,
    BG_JOB_TIMEOUT_SECS,
)
from langgraph_api.errors import (
    UserInterrupt,
    UserRollback,
)
from langgraph_api.js.errors import RemoteException
from langgraph_api.metadata import incr_runs
from langgraph_api.schema import Run
from langgraph_api.stream import (
    astream_state,
    consume,
)
from langgraph_api.utils import set_auth_ctx, with_user
from langgraph_storage.database import connect
from langgraph_storage.ops import Runs, Threads
from langgraph_storage.retry import RETRIABLE_EXCEPTIONS

try:
    from psycopg.errors import InFailedSqlTransaction
except ImportError:
    InFailedSqlTransaction = ()

logger = structlog.stdlib.get_logger(__name__)


class WorkerResult(TypedDict):
    checkpoint: CheckpointPayload | None
    status: str | None
    exception: Exception | None
    run: Run
    webhook: str | None
    run_started_at: str
    run_ended_at: str | None


@asynccontextmanager
async def set_auth_ctx_for_run(
    run_kwargs: dict, user_id: str | None = None
) -> AsyncGenerator[None, None]:
    # user_id is a fallback.
    try:
        user = run_kwargs["config"]["configurable"]["langgraph_auth_user"]
        permissions = run_kwargs["config"]["configurable"]["langgraph_auth_permissions"]
        user = normalize_user(user)
    except Exception:
        user = SimpleUser(user_id) if user_id is not None else None
        permissions = None
    if user is not None:
        async with with_user(user, permissions):
            yield None
    else:
        yield None


async def worker(
    run: Run,
    attempt: int,
    main_loop: asyncio.AbstractEventLoop,
) -> WorkerResult:
    run_id = run["run_id"]
    if attempt == 1:
        incr_runs()
    checkpoint: CheckpointPayload | None = None
    exception: Exception | None = None
    status: str | None = None
    webhook = run["kwargs"].get("webhook", None)
    run_started_at = datetime.now(UTC)
    run_ended_at: str | None = None

    async with (
        connect() as conn,
        set_auth_ctx_for_run(run["kwargs"]),
        Runs.enter(run_id, main_loop) as done,
    ):
        temporary = run["kwargs"].get("temporary", False)
        run_created_at = run["created_at"].isoformat()
        await logger.ainfo(
            "Starting background run",
            run_id=str(run_id),
            run_attempt=attempt,
            run_created_at=run_created_at,
            run_started_at=run_started_at.isoformat(),
            run_queue_ms=ms(run_started_at, run["created_at"]),
        )

        def on_checkpoint(checkpoint_arg: CheckpointPayload):
            nonlocal checkpoint
            checkpoint = checkpoint_arg

        def on_task_result(task_result: TaskResultPayload):
            if checkpoint is not None:
                for task in checkpoint["tasks"]:
                    if task["id"] == task_result["id"]:
                        task.update(task_result)
                        break

        try:
            if attempt > BG_JOB_MAX_RETRIES:
                raise RuntimeError(f"Run {run['run_id']} exceeded max attempts")
            if temporary:
                stream = astream_state(
                    AsyncExitStack(), conn, cast(Run, run), attempt, done
                )
            else:
                stream = astream_state(
                    AsyncExitStack(),
                    conn,
                    cast(Run, run),
                    attempt,
                    done,
                    on_checkpoint=on_checkpoint,
                    on_task_result=on_task_result,
                )
            await asyncio.wait_for(consume(stream, run_id), BG_JOB_TIMEOUT_SECS)
            run_ended_at = datetime.now(UTC).isoformat()
            await logger.ainfo(
                "Background run succeeded",
                run_id=str(run_id),
                run_attempt=attempt,
                run_created_at=run_created_at,
                run_started_at=run_started_at.isoformat(),
                run_ended_at=run_ended_at,
                run_exec_ms=ms(datetime.now(UTC), run_started_at),
            )
            status = "success"
            await Runs.set_status(conn, run_id, "success")
        except TimeoutError as e:
            exception = e
            status = "timeout"
            run_ended_at = datetime.now(UTC).isoformat()
            await logger.awarning(
                "Background run timed out",
                run_id=str(run_id),
                run_attempt=attempt,
                run_created_at=run_created_at,
                run_started_at=run_started_at.isoformat(),
                run_ended_at=run_ended_at,
                run_exec_ms=ms(datetime.now(UTC), run_started_at),
            )
            await Runs.set_status(conn, run_id, "timeout")
        except UserRollback as e:
            exception = e
            status = "rollback"
            run_ended_at = datetime.now(UTC).isoformat()
            try:
                await Runs.delete(conn, run_id, thread_id=run["thread_id"])
                await logger.ainfo(
                    "Background run rolled back",
                    run_id=str(run_id),
                    run_attempt=attempt,
                    run_created_at=run_created_at,
                    run_started_at=run_started_at.isoformat(),
                    run_ended_at=run_ended_at,
                    run_exec_ms=ms(datetime.now(UTC), run_started_at),
                )

            except InFailedSqlTransaction as e:
                await logger.ainfo(
                    "Ignoring rollback error",
                    run_id=str(run_id),
                    run_attempt=attempt,
                    run_created_at=run_created_at,
                    exc=str(e),
                )
                # We need to clean up the transaction early if we want to
                # update the thread status with the same connection
                await exit.aclose()
            except HTTPException as e:
                if e.status_code == 404:
                    await logger.ainfo(
                        "Ignoring rollback error for missing run",
                        run_id=str(run_id),
                        run_attempt=attempt,
                        run_created_at=run_created_at,
                    )
                else:
                    raise

            checkpoint = None  # reset the checkpoint
        except UserInterrupt as e:
            exception = e
            status = "interrupted"
            run_ended_at = datetime.now(UTC).isoformat()
            await logger.ainfo(
                "Background run interrupted",
                run_id=str(run_id),
                run_attempt=attempt,
                run_created_at=run_created_at,
                run_started_at=run_started_at.isoformat(),
                run_ended_at=run_ended_at,
                run_exec_ms=ms(datetime.now(UTC), run_started_at),
            )
            await Runs.set_status(conn, run_id, "interrupted")
        except RETRIABLE_EXCEPTIONS as e:
            exception = e
            status = "retry"
            run_ended_at = datetime.now(UTC).isoformat()
            await logger.awarning(
                "Background run failed, will retry",
                exc_info=True,
                run_id=str(run_id),
                run_attempt=attempt,
                run_created_at=run_created_at,
                run_started_at=run_started_at.isoformat(),
                run_ended_at=run_ended_at,
                run_exec_ms=ms(datetime.now(UTC), run_started_at),
            )
            await Runs.set_status(conn, run_id, "pending")
            raise
        except Exception as exc:
            exception = exc
            status = "error"
            run_ended_at = datetime.now(UTC).isoformat()
            await logger.aexception(
                "Background run failed",
                exc_info=not isinstance(exc, RemoteException),
                run_id=str(run_id),
                run_attempt=attempt,
                run_created_at=run_created_at,
                run_started_at=run_started_at.isoformat(),
                run_ended_at=run_ended_at,
                run_exec_ms=ms(datetime.now(UTC), run_started_at),
            )
            await Runs.set_status(conn, run_id, "error")
        set_auth_ctx(None, None)
        # delete or set status of thread
        if temporary:
            await Threads.delete(conn, run["thread_id"])
        else:
            try:
                await Threads.set_status(conn, run["thread_id"], checkpoint, exception)
            except HTTPException as e:
                if e.status_code == 404:
                    await logger.ainfo(
                        "Ignoring set_status error for missing thread", exc=str(e)
                    )
                else:
                    raise
        # Note we don't handle asyncio.CancelledError here, as we want to
        # let it bubble up and rollback db transaction, thus marking the run
        # as available to be picked up by another worker
    return {
        "checkpoint": checkpoint,
        "status": status,
        "run_started_at": run_started_at.isoformat(),
        "run_ended_at": run_ended_at,
        "run": run,
        "exception": exception,
        "webhook": webhook,
    }


def ms(after: datetime, before: datetime) -> int:
    return int((after - before).total_seconds() * 1000)
