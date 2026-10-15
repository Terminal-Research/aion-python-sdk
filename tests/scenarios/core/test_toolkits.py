"""A toolkit plugin served by a real ``aion serve``: a turn on the wire, artifact versions, and a refusal.

The deployment has the scripted toolkit installed - its directory is on the
server's ``PYTHONPATH``, so its ``aion.toolkits`` entry point is found the
way an installed package's is - and its extension in ``enabled_extensions``.
Each request carries the script of its turn in a data part, as an event of
the toolkit's own vocabulary.

What a turn becomes is the toolkit host's translation, checked here once
through the server: the stream opens and closes with the Task, the toolkit's
object lands whole in ``task.metadata`` under its URI, the status line
survives updates that only change that object, and every message is an
Event of the turn. The artifacts of the tasks of one context are numbered
versions, and ``find_artifact`` reads the latest back. A request the toolkit
refuses before its task exists is a JSON-RPC error carrying the refusal's
code, and leaves no task; so is a script its payload model rejects, with the
code the toolkit's descriptor names for that.

A cancel reaches the toolkit once, with the task's id, and the task ends
``CANCELED``; the turn's own result is the one outcome, so the open stream
closes with it. A turn that does not stop within the grace period is
cancelled without it, its open stream closing ``CANCELED`` all the same, and
a subscription made while the cancel waits opens at once. Work the toolkit
had delivered before the cancel answers ``COMPLETED``. What the toolkit was
asked is read from the report a later turn of the toolkit makes: the server
is another process.

A toolkit that runs one task per context refuses a second one there with
``context_busy`` naming the task that holds it, a message into its running
task as well, until that task ends; other contexts are not held back. A
message without a task id continues a task waiting for input only when the
task is the addressed handler's: a toolkit's directive starts a task of its
own beside the agent's waiting question, and the answer still reaches the
question.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict

from aion.core.toolkit import CANCEL
from tests.scenarios.harness import (
    EVENT_EXTENSION_URI,
    Ev,
    ScenarioClient,
    ServeProcess,
    artifact,
    assert_shape,
    chunks,
    final_task,
    status,
    task,
)
from tests.scenarios.commands import ask_answer_text
from tests.scenarios.harness.recorder import _payload_of, reply_texts, to_event
from tests.scenarios.toolkits import (
    COMPLETED,
    EXCLUSIVE_URI,
    TOOLKIT,
    reported,
    reports,
    rest_of,
    rpc_script,
    run_script,
    script,
    start_working,
)
from tests.support.scripted_toolkit.contract import URI

pytestmark = pytest.mark.toolkits

INVALID_PARAMS = -32602
TASK_NOT_CANCELABLE = -32002


@pytest.fixture(scope="session")
def toolkit_server(server_for) -> ServeProcess:
    """A deployment with the scripted toolkit installed and enabled."""
    return server_for(TOOLKIT)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def toolkit_client(toolkit_server) -> AsyncIterator[ScenarioClient]:
    """A client on the toolkit deployment."""
    scenario_client = await ScenarioClient.connect(toolkit_server.base_url, toolkit_server.variant.agent_id)
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


TURN = [
    {
        "kind": "message",
        "message": {"type": "stage.changed", "parts": [{"kind": "text", "text": "Preparing"}]},
        "task_metadata": {"stage": "preparing", "plan": {"done": 0, "total": 2}},
    },
    {"kind": "message", "message": {"parts": [{"kind": "text", "text": "Work"}]}, "partial": True},
    {"kind": "message", "message": {"parts": [{"kind": "text", "text": "ing"}]}, "partial": True},
    {"kind": "status", "task_metadata": {"plan": {"done": 1, "total": 2}}},
    {
        "kind": "artifact",
        "artifact": {"id": "report", "name": "Report", "parts": [{"kind": "text", "text": "All done"}]},
        "last_chunk": True,
    },
    {
        "kind": "result",
        "state": "completed",
        "message": {"parts": [{"kind": "text", "text": "Finished"}]},
        "task_metadata": {"stage": "finished", "plan": {"done": 2, "total": 2}},
    },
]


@pytest.mark.variant("toolkit")
async def test_a_toolkit_turn_streams_and_completes_with_its_object(toolkit_client: ScenarioClient) -> None:
    """Messages, deltas, details and an artifact on the stream; the object whole in the task."""
    envelope = script(outputs=TURN)

    events = await toolkit_client.send(
        "run", extensions=[URI], data=[envelope.data], message_metadata=envelope.message_metadata
    )

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        status("WORKING", text="Preparing"),
        chunks(2),
        status("WORKING", text=""),
        artifact(name="Report", last=True),
        task("COMPLETED", final=True),
    ])
    closing = final_task(events)
    expected = {"stage": "finished", "plan": {"done": 2, "total": 2}}
    assert closing.metadata[URI] == expected
    assert closing.text == "Finished"

    stored = await toolkit_client.get_task(closing.task_id)
    assert _plain(stored.metadata)[URI] == expected
    assert [item.artifact_id for item in stored.artifacts] == ["report"]
    preparing = next(message for message in stored.history if _text(message) == "Preparing")
    markup = _plain(preparing.metadata)[EVENT_EXTENSION_URI]
    assert (markup["type"], markup["source"], markup["id"]) == ("stage.changed", URI, preparing.message_id)


@pytest.mark.variant("toolkit")
async def test_a_status_update_keeps_the_status_line(toolkit_client: ScenarioClient) -> None:
    """Details without a message change the object, not what the task last said."""
    envelope = script(
        outputs=[
            {"kind": "message", "message": {"parts": [{"kind": "text", "text": "Halfway"}]}},
            {"kind": "status", "task_metadata": {"usage": {"tokens": 10}}},
            {"kind": "result", "state": "input-required"},
        ]
    )

    events = await toolkit_client.send(
        "run", extensions=[URI], data=[envelope.data], message_metadata=envelope.message_metadata
    )

    closing = final_task(events)
    assert closing.state == "INPUT_REQUIRED"
    assert closing.text == "Halfway"
    assert closing.metadata[URI] == {"usage": {"tokens": 10}}


@pytest.mark.variant("toolkit")
async def test_the_tasks_of_a_context_write_numbered_versions_of_an_artifact_and_find_the_latest(
    toolkit_client: ScenarioClient,
) -> None:
    """The server numbers a toolkit's artifact per context, and ``find_artifact`` reads the latest back."""
    context_id = f"versions-{uuid.uuid4().hex}"

    async def run(**fields: Any):
        envelope = script(**fields)
        events = await toolkit_client.send(
            "run",
            extensions=[URI],
            data=[envelope.data],
            message_metadata=envelope.message_metadata,
            context_id=context_id,
        )
        return await toolkit_client.get_task(final_task(events).task_id)

    def write(text: str) -> dict:
        return {
            "outputs": [
                {
                    "kind": "artifact",
                    "artifact": {"id": "evolution-spec", "parts": [{"kind": "text", "text": text}]},
                    "last_chunk": True,
                },
                {"kind": "result", "state": "completed"},
            ]
        }

    first = await run(**write("plan one"))
    second = await run(**write("plan two"))
    reader = await run(find_artifact="evolution-spec")

    assert [_plain(task.artifacts[0].metadata)["version"] for task in (first, second)] == ["0", "1"]
    [found] = [
        _plain(part.data)
        for message in reader.history
        for part in message.parts
        if part.WhichOneof("content") == "data" and "found" in _plain(part.data)
    ]
    assert found == {"found": True, "task_id": second.id, "version": 1, "text": "plan two"}


@pytest.mark.variant("toolkit")
async def test_a_refused_request_is_an_error_with_its_code_and_leaves_no_task(
    toolkit_client: ScenarioClient,
) -> None:
    """The toolkit's refusal is the JSON-RPC error, with its code; no task was opened for it."""
    context_id = f"refused-{uuid.uuid4().hex}"
    envelope = script(refuse={"code": "forbidden_target", "message": "No target.", "retryable": False})

    response = await toolkit_client.rpc(
        "SendMessage",
        {
            "message": {
                "messageId": uuid.uuid4().hex,
                "contextId": context_id,
                "role": "ROLE_USER",
                "extensions": [URI],
                "metadata": dict(envelope.message_metadata),
                "parts": [
                    {"text": "run"},
                    {"data": envelope.data.data, "metadata": dict(envelope.data.metadata)},
                ],
            }
        },
    )

    error = response["error"]
    assert error["code"] == INVALID_PARAMS
    assert error["message"] == "No target."
    assert error["data"][0]["metadata"] == {"code": "forbidden_target", "retryable": "false"}
    assert await toolkit_client.tasks_in_context(context_id) == []


@pytest.mark.variant("toolkit")
@pytest.mark.parametrize("uri", [URI, EXCLUSIVE_URI])
async def test_a_payload_the_toolkits_model_refuses_is_an_error_with_the_common_code_and_leaves_no_task(
    toolkit_client: ScenarioClient, uri: str
) -> None:
    """A malformed script never reaches the toolkit: every toolkit refuses it with ``invalid_payload``."""
    context_id = f"malformed-{uuid.uuid4().hex}"

    response = await rpc_script(toolkit_client, uri, context_id=context_id, no_such_field=True)

    error = response["error"]
    assert error["code"] == INVALID_PARAMS
    assert "no_such_field" in error["message"]
    assert error["data"][0]["metadata"] == {"code": "invalid_payload", "retryable": "false"}
    assert await toolkit_client.tasks_in_context(context_id) == []


async def _cancel_a_working_turn(client: ScenarioClient) -> tuple[str, list[Ev], TaskState.ValueType]:
    """Start a turn that waits for ``cancel()``, cancel it once it has spoken, and read the stream to its end.

    Returns:
        The task id, every event the stream delivered, and the state of the
        task the cancel request itself answered with.
    """
    envelope = script(
        outputs=[{"kind": "message", "message": {"parts": [{"kind": "text", "text": "Working"}]}}],
        wait_for_cancel=True,
    )
    events: list[Ev] = []
    task_id: str | None = None
    cancel_state: TaskState.ValueType | None = None

    stream = client.stream("run", extensions=[URI], data=[envelope.data], message_metadata=envelope.message_metadata)
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


@pytest.mark.variant("toolkit")
async def test_a_cancel_ends_the_turn_canceled_and_tells_the_toolkit(toolkit_client: ScenarioClient) -> None:
    """The toolkit is asked once, with the task's id and the grace period; the task ends ``CANCELED``."""
    task_id, events, cancel_state = await _cancel_a_working_turn(toolkit_client)

    assert cancel_state == TaskState.TASK_STATE_CANCELED
    closing = final_task(events)
    assert closing.state == "CANCELED"
    assert closing.final, "the terminal event did not close the stream"
    assert (await toolkit_client.get_task(task_id)).status.state == TaskState.TASK_STATE_CANCELED
    report = await reported(toolkit_client)
    assert [item for item in report["cancels"] if item["task_id"] == task_id] == [
        {"task_id": task_id, "reason": CANCEL, "within": 15}
    ]


@pytest.mark.variant("toolkit")
async def test_a_second_cancel_of_the_finished_task_is_refused_and_does_not_reach_the_toolkit(
    toolkit_client: ScenarioClient,
) -> None:
    """The repeat is an error, the task stays as it was, and the toolkit is not asked again."""
    task_id, _, _ = await _cancel_a_working_turn(toolkit_client)
    before = await toolkit_client.get_task(task_id)

    again = await toolkit_client.rpc("CancelTask", {"id": task_id})

    assert again.get("error", {}).get("code") == TASK_NOT_CANCELABLE, f"a finished task accepted a cancel: {again}"
    assert await toolkit_client.get_task(task_id) == before
    report = await reported(toolkit_client)
    assert [item["task_id"] for item in report["cancels"]].count(task_id) == 1


@pytest.mark.variant("toolkit")
async def test_a_turn_past_the_grace_period_is_cancelled_without_it_and_a_subscription_is_not_held(
    toolkit_client: ScenarioClient,
) -> None:
    """The cancel waits out the grace period; the stream and a subscription made meanwhile both close ``CANCELED``."""
    task_id, stream = await start_working(toolkit_client, URI, hang_on_cancel=True)
    cancelling = asyncio.create_task(toolkit_client.cancel(task_id))
    await asyncio.sleep(1)

    asked = time.monotonic()
    subscription = toolkit_client.subscribe_stream(task_id)
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


@pytest.mark.variant("toolkit")
async def test_a_cancel_after_the_work_was_delivered_answers_completed(toolkit_client: ScenarioClient) -> None:
    """The turn's own outcome stands: the cancel answers with it, and so does the stream."""
    task_id, stream = await start_working(toolkit_client, URI, completes_on_cancel=True)

    answered = await toolkit_client.cancel(task_id)
    streamed = await rest_of(stream)

    assert answered.status.state == TaskState.TASK_STATE_COMPLETED
    assert final_task(streamed).state == "COMPLETED"
    assert (await toolkit_client.get_task(task_id)).status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.variant("toolkit")
def test_the_card_offers_the_enabled_toolkit(toolkit_server: ServeProcess) -> None:
    """An installed toolkit the agent enables is an extension the card announces."""
    card = httpx.get(f"{toolkit_server.agent_url()}/.well-known/agent-card.json", timeout=10).json()

    assert URI in [extension["uri"] for extension in card["capabilities"]["extensions"]]


def _context_busy(response: dict) -> dict:
    """The ``ErrorInfo`` metadata of a ``context_busy`` refusal."""
    error = response.get("error")
    assert error is not None, f"the request was served: {response}"
    assert error["code"] == INVALID_PARAMS
    return error["data"][0]["metadata"]


@pytest.mark.variant("toolkit")
async def test_an_exclusive_toolkit_refuses_a_second_task_in_a_context_until_the_first_ends(
    toolkit_client: ScenarioClient,
) -> None:
    """A second directive, and a message into the running task, get ``context_busy`` naming the task."""
    context_id = f"exclusive-{uuid.uuid4().hex}"
    task_id, stream = await start_working(toolkit_client, EXCLUSIVE_URI, context_id=context_id)
    try:
        second = await rpc_script(toolkit_client, EXCLUSIVE_URI, context_id=context_id, outputs=[COMPLETED])
        into_the_task = await rpc_script(toolkit_client, EXCLUSIVE_URI, task_id=task_id, outputs=[COMPLETED])

        busy = {"code": "context_busy", "retryable": "true", "taskId": task_id}
        assert _context_busy(second) == busy
        assert _context_busy(into_the_task) == busy
        assert [task.id for task in await toolkit_client.tasks_in_context(context_id)] == [task_id]

        assert (await toolkit_client.cancel(task_id)).status.state == TaskState.TASK_STATE_CANCELED
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()

    after = await run_script(toolkit_client, EXCLUSIVE_URI, context_id=context_id, outputs=[COMPLETED])
    assert after.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.variant("toolkit")
async def test_an_exclusive_toolkit_runs_in_several_contexts_at_once(toolkit_client: ScenarioClient) -> None:
    """The limit is per context: two contexts each hold a running task of the toolkit."""
    first, first_stream = await start_working(toolkit_client, EXCLUSIVE_URI)
    second, second_stream = await start_working(toolkit_client, EXCLUSIVE_URI)
    try:
        assert first != second
        for task_id in (first, second):
            assert (await toolkit_client.get_task(task_id)).status.state == TaskState.TASK_STATE_WORKING
    finally:
        for task_id, stream in ((first, first_stream), (second, second_stream)):
            await toolkit_client.cancel(task_id)
            with contextlib.suppress(Exception):
                await stream.aclose()


@pytest.mark.variant("toolkit")
@pytest.mark.command("ask")
async def test_a_directive_beside_the_agents_waiting_question_starts_its_own_task(
    toolkit_client: ScenarioClient,
) -> None:
    """The directive is not taken as the answer: a toolkit task starts, and the question keeps waiting for it."""
    context_id = f"beside-a-question-{uuid.uuid4().hex}"
    asked = final_task(await toolkit_client.send("ask", context_id=context_id))
    assert asked.state == "INPUT_REQUIRED"

    directive = await run_script(toolkit_client, URI, context_id=context_id, report=True, outputs=[COMPLETED])

    [report] = reports(directive)
    assert directive.id != asked.task_id
    assert report["kind"] == "start"
    assert directive.status.state == TaskState.TASK_STATE_COMPLETED
    assert (await toolkit_client.get_task(asked.task_id)).status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    answer = await toolkit_client.send("use Python", context_id=context_id)
    answered = final_task(answer)
    assert (answered.task_id, answered.state) == (asked.task_id, "COMPLETED")
    assert ask_answer_text("use Python") in reply_texts(answer)


@pytest.mark.variant("toolkit")
async def test_a_toolkit_that_waits_for_input_continues_its_own_task(toolkit_client: ScenarioClient) -> None:
    """A directive without a task id resumes the toolkit's waiting task in the context."""
    context_id = f"toolkit-waits-{uuid.uuid4().hex}"
    waiting = await run_script(
        toolkit_client, URI, context_id=context_id, outputs=[{"kind": "result", "state": "input-required"}]
    )

    resumed = await run_script(toolkit_client, URI, context_id=context_id, report=True, outputs=[COMPLETED])

    [report] = reports(resumed)
    assert resumed.id == waiting.id
    assert (report["kind"], report["task_id"]) == ("resume", waiting.id)
    assert resumed.status.state == TaskState.TASK_STATE_COMPLETED


def _plain(struct) -> dict:
    return MessageToDict(struct)


def _text(message) -> str:
    return "".join(part.text for part in message.parts if part.text)
