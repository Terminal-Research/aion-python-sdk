"""The evolution extension served by a real ``aion serve``: the standard path, artifact versions, refusals, cancel.

The deployment enables the evolution extension with the stand-in toolkit
installed - its directory is on the server's ``PYTHONPATH``, where the SDK's
wrapper imports it from - and the daemon extension it requires. Each request
carries the script of its turn in the directive, and the daemon payload.

What a turn's events become is the server's standard path, checked here once
through the server: the stream opens and closes with the Task, the object the
executor writes under its URI lands whole in ``task.metadata``, the status
line survives updates that only change that object, and the artifacts of the
tasks of one context are numbered versions the next task reads back. A
request the toolkit refuses closes its task ``REJECTED`` with the refusal's
code; so does a directive its model rejects, with ``invalid_payload``.
Without the toolkit installed, an agent that enables the extension does not
start, and its log says why.

A cancel reaches the toolkit once, with the task's id, and the task ends
``CANCELED``, written by the server once the turn stopped, so the open stream
closes with it. A turn that does not stop within the grace period is
cancelled without it, its open stream closing ``CANCELED`` all the same, and
a subscription made while the cancel waits opens at once. A turn that closes
its task itself before the cancel lands keeps that outcome. What the toolkit
was asked is read from the report a later turn makes: the server is another
process.

Evolution runs one task per context: a second one there is rejected with
``context_busy`` naming the task that holds it, and a message into the
running task is refused with ``task_busy``, until that task ends; other
contexts are not held back. A message without a task id continues a task
waiting for input only when the task is the addressed executor's: a
directive starts a task of its own beside the agent's waiting question, and
the answer still reaches the question.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
import uuid
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from tests.scenarios.evolution import (
    COMPLETED,
    EVOLUTION,
    EXTENSIONS,
    URI,
    WORKING,
    data_parts,
    rejection,
    reported,
    reports,
    rest_of,
    rpc_script,
    run_script,
    script,
    start_working,
)
from tests.scenarios.harness import (
    Ev,
    ScenarioClient,
    ServeProcess,
    artifact,
    assert_shape,
    final_task,
    status,
    task,
)
from tests.scenarios.commands import ask_answer_text
from tests.scenarios.harness.daemon import daemon_metadata
from tests.scenarios.harness.recorder import _payload_of, reply_texts, to_event

pytestmark = pytest.mark.evolution

INVALID_PARAMS = -32602
TASK_NOT_CANCELABLE = -32002

WITHOUT_TOOLKIT = dataclasses.replace(EVOLUTION, name="evolution-without-toolkit", env={})
"""The same deployment without the toolkit installed: its agent does not start."""


@pytest.fixture(scope="session")
def evolution_server(server_for) -> ServeProcess:
    """A deployment with the evolution extension enabled and the stand-in toolkit installed."""
    return server_for(EVOLUTION)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def evolution_client(evolution_server) -> AsyncIterator[ScenarioClient]:
    """A client on the evolution deployment."""
    scenario_client = await ScenarioClient.connect(evolution_server.base_url, evolution_server.variant.agent_id)
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


async def _send(client: ScenarioClient, *, context_id: str | None = None, **fields: Any) -> list[Ev]:
    envelope = script(**fields)
    return await client.send(
        "run",
        extensions=EXTENSIONS,
        metadata=daemon_metadata(),
        data=[envelope.data],
        message_metadata=envelope.message_metadata,
        **({"context_id": context_id} if context_id else {}),
    )


TURN = [
    {"kind": "status", "text": "Preparing", "metadata": {URI: {"stage": "preparing", "plan": {"done": 0, "total": 2}}}},
    {"kind": "status", "metadata": {URI: {"stage": "preparing", "plan": {"done": 1, "total": 2}}}},
    {"kind": "artifact", "id": "report", "text": "All done"},
    {
        "kind": "status",
        "state": "completed",
        "text": "Finished",
        "metadata": {URI: {"stage": "finished", "plan": {"done": 2, "total": 2}}},
    },
]


@pytest.mark.variant("evolution")
async def test_a_turn_takes_the_standard_path_and_completes_with_its_object(evolution_client: ScenarioClient) -> None:
    """Status updates and an artifact on the stream; the object the executor writes whole in the task."""
    events = await _send(evolution_client, outputs=TURN)

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text="Preparing"),
        status("WORKING", text=""),
        artifact(last=True),
        task("COMPLETED", final=True),
    ])
    closing = final_task(events)
    expected = {"stage": "finished", "plan": {"done": 2, "total": 2}}
    assert closing.metadata[URI] == expected
    assert closing.text == "Finished"

    stored = await evolution_client.get_task(closing.task_id)
    assert _plain(stored.metadata)[URI] == expected
    assert [item.artifact_id for item in stored.artifacts] == ["report"]
    assert _plain(stored.artifacts[0].metadata)["version"] == "0"


@pytest.mark.variant("evolution")
async def test_a_status_update_keeps_the_status_line(evolution_client: ScenarioClient) -> None:
    """Details without a message change the object, not what the task last said."""
    events = await _send(
        evolution_client,
        outputs=[
            {"kind": "status", "text": "Halfway"},
            {"kind": "status", "metadata": {URI: {"usage": {"tokens": 10}}}},
            {"kind": "status", "state": "input-required"},
        ],
    )

    closing = final_task(events)
    assert closing.state == "INPUT_REQUIRED"
    assert closing.text == "Halfway"
    assert closing.metadata[URI] == {"usage": {"tokens": 10}}


@pytest.mark.variant("evolution")
async def test_the_tasks_of_a_context_write_numbered_versions_of_an_artifact_and_find_the_latest(
    evolution_client: ScenarioClient,
) -> None:
    """The executor's artifacts are numbered per context, and the next task reads the latest back."""
    context_id = f"versions-{uuid.uuid4().hex}"

    async def run(**fields: Any):
        return await evolution_client.get_task(
            final_task(await _send(evolution_client, context_id=context_id, **fields)).task_id
        )

    def write(text: str) -> dict:
        return {"outputs": [{"kind": "artifact", "id": "evolution-spec", "text": text}, COMPLETED]}

    first = await run(**write("plan one"))
    second = await run(**write("plan two"))
    reader = await run(find_artifact="evolution-spec", outputs=[COMPLETED])

    assert [_plain(task.artifacts[0].metadata)["version"] for task in (first, second)] == ["0", "1"]
    [found] = [data for data in data_parts(reader) if "found" in data]
    assert found == {"found": True, "task_id": second.id, "version": 1, "text": "plan two"}


@pytest.mark.variant("evolution")
async def test_a_refused_request_closes_its_task_rejected_with_the_code(
    evolution_client: ScenarioClient,
) -> None:
    """The toolkit's refusal is the task's outcome, with its code and message."""
    context_id = f"refused-{uuid.uuid4().hex}"

    response = await rpc_script(
        evolution_client,
        context_id=context_id,
        refuse={"code": "forbidden_target", "message": "No target.", "retryable": False},
    )

    assert rejection(response) == {"code": "forbidden_target", "message": "No target.", "retryable": False}
    [stored] = await evolution_client.tasks_in_context(context_id)
    assert stored.status.state == TaskState.TASK_STATE_REJECTED


@pytest.mark.variant("evolution")
async def test_a_directive_its_model_refuses_is_rejected_with_the_common_code(
    evolution_client: ScenarioClient,
) -> None:
    """A malformed directive never reaches the toolkit: its task is rejected with ``invalid_payload``."""
    context_id = f"malformed-{uuid.uuid4().hex}"

    response = await rpc_script(evolution_client, context_id=context_id, no_such_field=True)

    refused = rejection(response)
    assert (refused["code"], refused["retryable"]) == ("invalid_payload", False)
    assert "no_such_field" in refused["message"]


async def _cancel_a_working_turn(client: ScenarioClient) -> tuple[str, list[Ev], TaskState.ValueType]:
    """Start a turn that waits for ``cancel()``, cancel it once it has spoken, and read the stream to its end.

    Returns:
        The task id, every event the stream delivered, and the state of the
        task the cancel request itself answered with.
    """
    envelope = script(outputs=[WORKING], wait_for_cancel=True)
    events: list[Ev] = []
    task_id: str | None = None
    cancel_state: TaskState.ValueType | None = None

    stream = client.stream(
        "run",
        metadata=daemon_metadata(),
        extensions=EXTENSIONS,
        data=[envelope.data],
        message_metadata=envelope.message_metadata,
    )
    try:
        async for response in stream:
            event = to_event(_payload_of(response))
            events.append(event)
            if event.task_id and task_id is None:
                task_id = event.task_id
            if event.state == "WORKING" and event.text == "Working" and task_id:
                cancel_state = (await client.cancel(task_id)).status.state
                break

        assert task_id is not None and cancel_state is not None, f"the turn never spoke: {events}"
        async for response in stream:
            events.append(to_event(_payload_of(response)))
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()
    return task_id, events, cancel_state


@pytest.mark.variant("evolution")
async def test_a_cancel_ends_the_turn_canceled_and_tells_the_toolkit(evolution_client: ScenarioClient) -> None:
    """The toolkit is asked once, with the task's id and the grace period; the task ends ``CANCELED``."""
    task_id, events, cancel_state = await _cancel_a_working_turn(evolution_client)

    assert cancel_state == TaskState.TASK_STATE_CANCELED
    closing = final_task(events)
    assert closing.state == "CANCELED"
    assert closing.final, "the terminal event did not close the stream"
    assert (await evolution_client.get_task(task_id)).status.state == TaskState.TASK_STATE_CANCELED
    report = await reported(evolution_client)
    assert [item for item in report["cancels"] if item["task_id"] == task_id] == [
        {"task_id": task_id, "within": 15}
    ]


@pytest.mark.variant("evolution")
async def test_a_second_cancel_of_the_finished_task_is_refused_and_does_not_reach_the_toolkit(
    evolution_client: ScenarioClient,
) -> None:
    """The repeat is an error, the task stays as it was, and the toolkit is not asked again."""
    task_id, _, _ = await _cancel_a_working_turn(evolution_client)
    before = await evolution_client.get_task(task_id)

    again = await evolution_client.rpc("CancelTask", {"id": task_id})

    assert again.get("error", {}).get("code") == TASK_NOT_CANCELABLE, f"a finished task accepted a cancel: {again}"
    assert await evolution_client.get_task(task_id) == before
    report = await reported(evolution_client)
    assert [item["task_id"] for item in report["cancels"]].count(task_id) == 1


@pytest.mark.variant("evolution")
async def test_a_turn_past_the_grace_period_is_cancelled_without_it_and_a_subscription_is_not_held(
    evolution_client: ScenarioClient,
) -> None:
    """The cancel waits out the grace period; the stream and a subscription made meanwhile both close ``CANCELED``."""
    task_id, stream = await start_working(evolution_client, hang_on_cancel=True)
    cancelling = asyncio.create_task(evolution_client.cancel(task_id))
    await asyncio.sleep(1)

    asked = time.monotonic()
    subscription = evolution_client.subscribe_stream(task_id)
    opening = to_event(_payload_of(await anext(subscription)))
    opened_after = time.monotonic() - asked
    subscribed = [opening, *await rest_of(subscription)]
    streamed = await rest_of(stream)
    answered = await cancelling

    assert opened_after < 5, f"the subscription waited {opened_after:.0f}s on the cancel"
    assert (opening.kind, opening.state) == ("task", "WORKING")
    assert answered.status.state == TaskState.TASK_STATE_CANCELED
    assert final_task(subscribed).state == "CANCELED"
    assert final_task(streamed).state == "CANCELED"


@pytest.mark.variant("evolution")
async def test_a_turn_that_closes_its_task_on_the_cancel_keeps_its_outcome(evolution_client: ScenarioClient) -> None:
    """The turn's own outcome stands: the cancel answers with it, and so does the stream."""
    task_id, stream = await start_working(evolution_client, completes_on_cancel=True)

    answered = await evolution_client.cancel(task_id)
    streamed = await rest_of(stream)

    assert answered.status.state == TaskState.TASK_STATE_COMPLETED
    assert final_task(streamed).state == "COMPLETED"
    assert (await evolution_client.get_task(task_id)).status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.variant("evolution")
def test_the_card_offers_the_enabled_evolution(evolution_server: ServeProcess) -> None:
    """An enabled evolution whose toolkit is installed is an extension the card announces, without its options."""
    card = httpx.get(f"{evolution_server.agent_url()}/.well-known/agent-card.json", timeout=10).json()

    [offered] = [extension for extension in card["capabilities"]["extensions"] if extension["uri"] == URI]
    assert "params" not in offered


@pytest.mark.variant("evolution-without-toolkit")
def test_without_the_toolkit_an_agent_that_enables_evolution_does_not_start(server_for) -> None:
    """``aion serve`` exits, and its output says what the agent lacks."""
    with pytest.raises(RuntimeError, match="exited with code") as failed:
        server_for(WITHOUT_TOOLKIT)

    assert "the behavior-evolution toolkit is not installed" in str(failed.value)


def _task_busy(response: dict) -> dict:
    """The ``ErrorInfo`` metadata of a ``task_busy`` refusal of a message into a running task."""
    error = response.get("error")
    assert error is not None, f"the request was served: {response}"
    assert error["code"] == INVALID_PARAMS
    return error["data"][0]["metadata"]


@pytest.mark.variant("evolution")
async def test_evolution_refuses_a_second_task_in_a_context_until_the_first_ends(
    evolution_client: ScenarioClient,
) -> None:
    """A second directive is rejected with ``context_busy``, and a message into the running task refused with ``task_busy``."""
    context_id = f"exclusive-{uuid.uuid4().hex}"
    task_id, stream = await start_working(evolution_client, context_id=context_id)
    try:
        second = await rpc_script(evolution_client, context_id=context_id, outputs=[COMPLETED])
        into_the_task = await rpc_script(evolution_client, task_id=task_id, outputs=[COMPLETED])

        refused = rejection(second)
        assert (refused["code"], refused["retryable"]) == ("context_busy", True)
        assert task_id in refused["message"]
        assert _task_busy(into_the_task) == {"code": "task_busy", "retryable": "true", "taskId": task_id}
        assert (await evolution_client.get_task(task_id)).status.state == TaskState.TASK_STATE_WORKING

        assert (await evolution_client.cancel(task_id)).status.state == TaskState.TASK_STATE_CANCELED
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    after = await run_script(evolution_client, context_id=context_id, outputs=[COMPLETED])
    assert after.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.variant("evolution")
async def test_evolution_runs_in_several_contexts_at_once(evolution_client: ScenarioClient) -> None:
    """The limit is per context: two contexts each hold a running evolution."""
    first, first_stream = await start_working(evolution_client)
    second, second_stream = await start_working(evolution_client)
    try:
        assert first != second
        for task_id in (first, second):
            assert (await evolution_client.get_task(task_id)).status.state == TaskState.TASK_STATE_WORKING
    finally:
        for task_id, stream in ((first, first_stream), (second, second_stream)):
            await evolution_client.cancel(task_id)
            with contextlib.suppress(Exception):
                await stream.aclose()


@pytest.mark.variant("evolution")
@pytest.mark.command("ask")
async def test_a_directive_beside_the_agents_waiting_question_starts_its_own_task(
    evolution_client: ScenarioClient,
) -> None:
    """The directive is not taken as the answer: an evolution task starts, and the question keeps waiting for it."""
    context_id = f"beside-a-question-{uuid.uuid4().hex}"
    asked = final_task(await evolution_client.send("ask", context_id=context_id))
    assert asked.state == "INPUT_REQUIRED"

    directive = await run_script(evolution_client, context_id=context_id, report=True, outputs=[COMPLETED])

    [report] = reports(directive)
    assert directive.id != asked.task_id
    assert report["kind"] == "start"
    assert directive.status.state == TaskState.TASK_STATE_COMPLETED
    assert (await evolution_client.get_task(asked.task_id)).status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    answer = await evolution_client.send("use Python", context_id=context_id)
    answered = final_task(answer)
    assert (answered.task_id, answered.state) == (asked.task_id, "COMPLETED")
    assert ask_answer_text("use Python") in reply_texts(answer)


@pytest.mark.variant("evolution")
async def test_an_evolution_that_waits_for_input_continues_its_own_task(evolution_client: ScenarioClient) -> None:
    """A directive without a task id resumes the extension's waiting task in the context."""
    context_id = f"evolution-waits-{uuid.uuid4().hex}"
    waiting = await run_script(
        evolution_client, context_id=context_id, outputs=[{"kind": "status", "state": "input-required"}]
    )

    resumed = await run_script(evolution_client, context_id=context_id, report=True, outputs=[COMPLETED])

    [report] = reports(resumed)
    assert resumed.id == waiting.id
    assert (report["kind"], report["task_id"]) == ("resume", waiting.id)
    assert resumed.status.state == TaskState.TASK_STATE_COMPLETED


def _plain(struct) -> dict:
    return MessageToDict(struct)


def _text(message) -> str:
    return "".join(part.text for part in message.parts if part.text)
