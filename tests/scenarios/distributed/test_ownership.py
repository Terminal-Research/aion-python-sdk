"""Who owns a task, when two servers of one agent share one database.

Every other scenario in the suite drives one server. These drive two - two
operating-system processes, two proxies, one PostgreSQL - because that is
where ownership is decided and the only place its assembly can be wrong. The
integration suite already proves the fenced statements themselves, with
several providers inside one interpreter; what it cannot show is a lease
taken under one identity and a row written under another, or an enforcement
switch that lives in a component nobody wired up.

So the questions here are only the ones that need two processes: does a task
started through one server belong to exactly that server, do the other
server's public operations on it refuse rather than misbehave, does the
refusal say who holds it, and is the claim let go when the turn ends.

Two of them are about what the refusal is *not*: not a 500 and not a silent
second execution, and not a decision made by reading a host name.
"""

from __future__ import annotations

import contextlib
from typing import AsyncIterator

import pytest
from a2a.types import TaskState

from tests.scenarios.commands import echo_text
from tests.scenarios.harness import (
    ScenarioClient,
    ServeProcess,
    final_task,
    run_until_working,
    stored_texts,
)
from tests.scenarios.harness.pg import claim_held, claim_released

pytestmark = [pytest.mark.distributed]

SLOW_SECONDS = 20
"""Long enough that the task is still running when the other server is asked."""

BUSY_CODE = -32050
"""The JSON-RPC code the SDK reserves for a task owned elsewhere."""

BUSY_REASON = "TASK_OWNERSHIP_BUSY"
"""``google.rpc.ErrorInfo`` reason carried in the error's data."""

CONTINUATION = "echo continue-me"
"""What the second server is asked to add to someone else's task."""


@contextlib.asynccontextmanager
async def running_task(owner: ScenarioClient) -> AsyncIterator[str]:
    """A task the owner is executing for the duration of the block."""
    task_id, _ = await run_until_working(owner, f"slow {SLOW_SECONDS}")
    try:
        yield task_id
    finally:
        with contextlib.suppress(Exception):
            await owner.cancel(task_id)


def refusal(response: dict) -> dict:
    """The error of a JSON-RPC response, or a failure showing what came instead."""
    assert "error" in response, f"the request was not refused: {response}"
    return response["error"]


def error_info(error: dict) -> dict:
    """The ``google.rpc.ErrorInfo`` entry of an error's data."""
    for entry in error.get("data") or []:
        if isinstance(entry, dict) and "reason" in entry:
            return entry
    raise AssertionError(f"the refusal carries no ErrorInfo: {error}")


# --------------------------------------------------------------------------
# The other server's public operations on someone else's running task
# --------------------------------------------------------------------------

@pytest.mark.command("slow")
async def test_the_other_server_refuses_to_subscribe_to_a_running_task(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """A subscriber on the wrong server is told so, by the reserved code."""
    first, second = clients

    async with running_task(first) as task_id:
        error = refusal(await second.rpc("SubscribeToTask", {"id": task_id}))

        assert error["code"] == BUSY_CODE, (
            f"a refusal that is not the reserved ownership code: {error}"
        )
        assert error_info(error)["reason"] == BUSY_REASON


@pytest.mark.command("slow")
async def test_the_other_server_refuses_to_continue_a_running_task(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """A message sent to the wrong server is refused the same way as a subscribe.

    The two are separate operations on the same task and separate paths
    through the server, so both are asked: one attaches a reader, the other
    would start work.
    """
    first, second = clients

    async with running_task(first) as task_id:
        error = refusal(
            await second.rpc(
                "SendMessage",
                {
                    "message": {
                        "messageId": "continue-" + task_id,
                        "role": "ROLE_USER",
                        "parts": [{"text": CONTINUATION}],
                        "taskId": task_id,
                    }
                },
            )
        )

        assert error["code"] == BUSY_CODE, (
            f"a refusal that is not the reserved ownership code: {error}"
        )
        assert error_info(error)["reason"] == BUSY_REASON


@pytest.mark.command("slow")
async def test_a_refused_continuation_starts_nothing(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """The refusal is the whole outcome: no second turn, no second task."""
    first, second = clients

    async with running_task(first) as task_id:
        before = await first.get_task(task_id)
        with pytest.raises(Exception) as refused:
            await second.send(CONTINUATION, task_id=task_id)

        assert str(BUSY_CODE) in str(refused.value), (
            f"the typed client saw something other than an ownership refusal: {refused.value}"
        )

        after = await first.get_task(task_id)
        assert after.history == before.history, (
            "the refused message was added to the task anyway"
        )
        assert echo_text(CONTINUATION) not in [
            part.text for message in after.history for part in message.parts
        ]
        assert [task.id for task in await second.tasks_in_context(before.context_id)] == [
            task_id
        ], "the refused request opened a task of its own"


# --------------------------------------------------------------------------
# What the refusal says, and what it does not depend on
# --------------------------------------------------------------------------

@pytest.mark.command("slow")
async def test_the_refusal_names_the_owning_instance(
    clients: tuple[ScenarioClient, ScenarioClient],
    servers: tuple[ServeProcess, ServeProcess],
) -> None:
    """The diagnostic reaches the caller, and it names the server that holds it."""
    first, second = clients
    owner_host = servers[0].variant.env["HOST_NAME"]
    assert owner_host != servers[1].variant.env["HOST_NAME"], (
        "this scenario needs two differently named servers to mean anything"
    )

    async with running_task(first) as task_id:
        error = refusal(await second.rpc("SubscribeToTask", {"id": task_id}))
        metadata = error_info(error)["metadata"]

        assert metadata["task_id"] == task_id
        assert metadata["owner_instance_id"] == owner_host


@pytest.mark.command("slow")
async def test_a_competitor_with_the_same_host_name_is_refused_too(
    same_host_clients: tuple[ScenarioClient, ScenarioClient],
    same_host_servers: tuple[ServeProcess, ServeProcess],
) -> None:
    """Authority is the fencing token, not the name of the process holding it.

    Both servers here call themselves the same thing, so the reported owner
    is indistinguishable from the asking process. The refusal is unchanged,
    which is the point: ``current_owner`` is a diagnostic - "best-effort,
    non-authoritative ... never used to grant or claim ownership" - and what
    decides is the claim row.
    """
    first, second = same_host_clients
    assert (
        same_host_servers[0].variant.env["HOST_NAME"]
        == same_host_servers[1].variant.env["HOST_NAME"]
    ), "this scenario needs two identically named servers to mean anything"

    async with running_task(first) as task_id:
        error = refusal(await second.rpc("SubscribeToTask", {"id": task_id}))

        assert error["code"] == BUSY_CODE
        assert error_info(error)["metadata"]["owner_instance_id"] == (
            same_host_servers[1].variant.env["HOST_NAME"]
        ), "the owner reported is not even distinguishable from the asking server"


# --------------------------------------------------------------------------
# What happens when the turn ends
# --------------------------------------------------------------------------

@pytest.mark.command("slow")
async def test_a_running_task_holds_a_claim_and_lets_it_go(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """A claim exists exactly while the work does."""
    first, _ = clients

    async with running_task(first) as task_id:
        assert await claim_held(task_id), "a running task holds no claim"

    await claim_released(task_id)


@pytest.mark.command("echo")
async def test_both_servers_read_the_same_finished_task(
    clients: tuple[ScenarioClient, ScenarioClient],
) -> None:
    """One database, one answer: the outcome does not depend on who is asked."""
    first, second = clients

    events = await first.send("echo two-servers-one-database")
    task_id = final_task(events).task_id

    await claim_released(task_id)
    here = await first.get_task(task_id)
    there = await second.get_task(task_id)

    assert here.status.state == TaskState.TASK_STATE_COMPLETED
    assert here == there, (
        f"the two servers disagree about the task.\non A:\n{here}\non B:\n{there}"
    )
    assert echo_text("two-servers-one-database") in stored_texts(there)
