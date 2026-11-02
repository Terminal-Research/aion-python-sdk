"""Answers handed to the server as finished A2A objects rather than spoken.

Everywhere else the agent speaks through the thread and the SDK decides what
that becomes on the wire. The outbox is the other door: the graph returns the
A2A payload itself, and the server's job is to apply it without losing what it
carries and without letting it overwrite what the server owns.
"""

from __future__ import annotations

from uuid import uuid4

from a2a.types import Artifact, Message, Part, Role, Task, TaskState, TaskStatus
from aion.core.a2a import A2AOutbox

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import (
    OUTBOX_FAILED_REASON,
    OUTBOX_MESSAGE_TEXT,
    OUTBOX_PAUSE_QUESTION,
    OUTBOX_PREFACE_TEXT,
    OUTBOX_TASK_ARTIFACT_NAME,
    OUTBOX_TASK_ARTIFACT_TEXT,
    OUTBOX_TASK_HISTORY_TEXT,
    OUTBOX_TASK_METADATA,
)

__all__ = [
    "outbox_after_reply",
    "outbox_failed",
    "outbox_failed_after_reply",
    "outbox_history",
    "outbox_message",
    "outbox_pause",
    "outbox_task",
]


async def outbox_message(invocation: Invocation) -> dict:
    """Return one A2A `Message` through the outbox."""
    return {
        "a2a_outbox": A2AOutbox(
            message=Message(
                message_id=uuid4().hex,
                role=Role.ROLE_AGENT,
                parts=[Part(text=OUTBOX_MESSAGE_TEXT)],
            )
        )
    }


async def outbox_task(invocation: Invocation) -> dict:
    """Return a `Task` patch: history, an artifact and metadata in one payload.

    Ids are left unset on purpose. They are the server's to fill in, and a
    scenario checks that the task the caller receives carries the server's
    ids rather than anything the agent could have put here.
    """
    return {
        "a2a_outbox": A2AOutbox(
            task=Task(
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                history=[
                    Message(
                        message_id=uuid4().hex,
                        role=Role.ROLE_AGENT,
                        parts=[Part(text=OUTBOX_TASK_HISTORY_TEXT)],
                    )
                ],
                artifacts=[
                    Artifact(
                        artifact_id=uuid4().hex,
                        name=OUTBOX_TASK_ARTIFACT_NAME,
                        parts=[Part(text=OUTBOX_TASK_ARTIFACT_TEXT)],
                    )
                ],
                metadata=OUTBOX_TASK_METADATA,
            )
        )
    }


def _agent_message(text: str) -> Message:
    return Message(message_id=uuid4().hex, role=Role.ROLE_AGENT, parts=[Part(text=text)])


async def outbox_history(invocation: Invocation) -> dict:
    """Return a `Task` patch that only adds a message, with no artifact of its own."""
    return {"a2a_outbox": A2AOutbox(task=Task(history=[_agent_message(OUTBOX_TASK_HISTORY_TEXT)]))}


async def outbox_failed(invocation: Invocation) -> dict:
    """Fail the task as an agent building its own Task does: a FAILED status that says why."""
    return {
        "a2a_outbox": A2AOutbox(
            task=Task(
                status=TaskStatus(
                    state=TaskState.TASK_STATE_FAILED,
                    message=_agent_message(OUTBOX_FAILED_REASON),
                )
            )
        )
    }


async def outbox_pause(invocation: Invocation) -> dict:
    """Leave the task waiting for input without an interrupt: an INPUT_REQUIRED status that asks."""
    return {
        "a2a_outbox": A2AOutbox(
            task=Task(
                status=TaskStatus(
                    state=TaskState.TASK_STATE_INPUT_REQUIRED,
                    message=_agent_message(OUTBOX_PAUSE_QUESTION),
                )
            )
        )
    }


async def outbox_after_reply(invocation: Invocation) -> dict:
    """Say something through the thread first, then hand over a `Message`."""
    await invocation.thread.reply(OUTBOX_PREFACE_TEXT)
    return await outbox_message(invocation)


async def outbox_failed_after_reply(invocation: Invocation) -> dict:
    """Explain through the thread first, then fail through the outbox."""
    await invocation.thread.reply(OUTBOX_PREFACE_TEXT)
    return await outbox_failed(invocation)
