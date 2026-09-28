"""Answers handed to the server as finished A2A objects rather than spoken.

Everywhere else the agent speaks through the thread and the SDK decides what
that becomes on the wire. The outbox is the other door: the agent yields an
event whose state delta carries the A2A payload itself, and the server's job
is to apply it without losing what it carries and without letting it overwrite
what the server owns.
"""

from __future__ import annotations

from uuid import uuid4

from a2a.types import Artifact, Message, Part, Role, Task, TaskState, TaskStatus
from aion.core.a2a import A2AOutbox
from google.adk.events import Event, EventActions

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import (
    OUTBOX_MESSAGE_TEXT,
    OUTBOX_TASK_ARTIFACT_NAME,
    OUTBOX_TASK_ARTIFACT_TEXT,
    OUTBOX_TASK_HISTORY_TEXT,
    OUTBOX_TASK_METADATA,
)

AGENT_NAME = "scenario_agent"

__all__ = ["outbox_message", "outbox_task"]


def _carrying(outbox: A2AOutbox) -> Event:
    """The ADK event that hands an outbox to the server."""
    return Event(
        author=AGENT_NAME,
        actions=EventActions(state_delta={"a2a_outbox": outbox}),
    )


async def outbox_message(invocation: Invocation) -> Event:
    """Return one A2A `Message` through the outbox."""
    return _carrying(
        A2AOutbox(
            message=Message(
                message_id=uuid4().hex,
                role=Role.ROLE_AGENT,
                parts=[Part(text=OUTBOX_MESSAGE_TEXT)],
            )
        )
    )


async def outbox_task(invocation: Invocation) -> Event:
    """Return a `Task` patch: history, an artifact and metadata in one payload.

    Ids are left unset on purpose. They are the server's to fill in, and a
    scenario checks that the task the caller receives carries the server's
    ids rather than anything the agent could have put here.
    """
    return _carrying(
        A2AOutbox(
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
    )
