"""Utility functions for inspecting A2A task and message objects."""

from typing import Optional

from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.core.a2a.enums import ArtifactId
from aion.server.a2a.constants import EPHEMERAL_STATUS_METADATA_KEY, INTERRUPT_TASK_STATES

__all__ = [
    "is_task_interrupted",
    "task_history_message_ids",
    "is_message_in_task_history",
    "extract_input_preview",
    "extract_event_preview",
    "empty_input_warning",
    "describe_event",
    "describe_artifact",
    "task_state_name",
    "mark_status_event_ephemeral",
    "is_ephemeral_status_event",
    "NO_TEXT",
]

NO_TEXT = "<no text>"
"""What a preview returns when the thing previewed carries no text at all."""

RESERVED_ARTIFACT_LABELS = {
    ArtifactId.STREAM_DELTA.value: "streaming text chunk",
    ArtifactId.THINKING_DELTA.value: "streaming reasoning chunk",
    ArtifactId.EPHEMERAL_MESSAGE.value: "ephemeral, not persisted",
    ArtifactId.REACTION.value: "reaction, not persisted",
}


def describe_artifact(artifact_id: str) -> str:
    """Names an artifact the way a log line should.

    Reserved Aion ids carry their meaning, but only to a reader who knows them
    by heart, so they are annotated: a stream delta is the agent typing at the
    user, while an ephemeral message or a reaction is shown once and never
    persisted. An artifact the agent named itself is left alone — its id is
    already its meaning.

    Args:
        artifact_id: The id the artifact was emitted under.

    Returns:
        The id, followed by what it means when Aion reserved it.
    """
    label = RESERVED_ARTIFACT_LABELS.get(artifact_id)
    name = artifact_id or '<unidentified>'
    return f'{name} ({label})' if label else name


def task_state_name(state: int) -> str:
    """Renders a task state as its declared name.

    Args:
        state: The enum value carried by an event.

    Returns:
        The enum name, or the raw value when this build does not know it —
        proto3 keeps unrecognised enum numbers, so a peer on a newer schema
        must not turn a log line into a traceback.
    """
    try:
        return TaskState.Name(state)
    except ValueError:
        return str(state)


def describe_event(event: object) -> str:
    """Names the outbound event a log line is about.

    A turn emits many events to the same destination — a status update per
    transition, an artifact update per streamed chunk, and the terminal Task.
    Without a discriminator every line reads the same and the log cannot answer
    the questions it is opened with: whether the outcome landed or only an
    intermediate ``WORKING``, and what the agent was sending in between.

    Reserved Aion artifacts are annotated with what they mean — see
    ``describe_artifact``.

    A status update names its message when it carries one, because ``WORKING``
    is both the transition into a running task and the state every reply the
    agent speaks is delivered under: without the id a turn of three replies logs
    three identical lines. The id is also the handle the rest of the system uses
    — the deduplicator keys on it, and it is what task history stores — so a
    reply that was logged as delivered can be followed into the stored task.

    Shared by the push and the streaming path so the same event reads the same
    way whichever channel delivered it.

    Args:
        event: The event being delivered.

    Returns:
        A short, single-line description of the event.
    """
    if isinstance(event, Task):
        return f'Task state={task_state_name(event.status.state)}'
    if isinstance(event, TaskStatusUpdateEvent):
        return (
            f'TaskStatusUpdateEvent '
            f'state={task_state_name(event.status.state)}'
            f'{_message_suffix(event.status)}'
        )
    if isinstance(event, TaskArtifactUpdateEvent):
        # Only the raised flag is printed. A delta artifact is closed by the
        # finished reply arriving as a status update rather than by a final
        # chunk, so ``last_chunk=False`` is the standing state of every chunk in
        # a turn — a constant, and a constant on a log line is noise.
        return (
            f'TaskArtifactUpdateEvent '
            f'artifact={describe_artifact(event.artifact.artifact_id)}'
            f'{" last_chunk=True" if event.last_chunk else ""}'
        )
    if isinstance(event, Message):
        return f'Message message_id={event.message_id or "<unidentified>"}'
    return type(event).__name__


def _message_suffix(status: TaskStatus) -> str:
    """Names the message a status update carries, when it carries one.

    Args:
        status: The status the update announces.

    Returns:
        A leading-space fragment to append to a log line, empty when the update
        is a bare transition. A message with no id of its own is reported as
        such rather than skipped: every producer assigns one, so an anonymous
        message is a defect worth seeing — and it is the message the
        deduplicator cannot match against task history.
    """
    if not status.HasField('message'):
        return ''
    return f' message_id={status.message.message_id or "<unidentified>"}'


def mark_status_event_ephemeral(event: TaskStatusUpdateEvent) -> None:
    """Flag a status update as ephemeral.

    An ephemeral status update is streamed to the client for live progress but
    never persisted into task history — see
    ``AionTaskManager._check_process_skip_event``, which drops flagged events
    before the base manager can fold their message into history.
    """
    event.metadata[EPHEMERAL_STATUS_METADATA_KEY] = True


def is_ephemeral_status_event(event: object) -> bool:
    """Return True when ``event`` is a status update flagged ephemeral."""
    if not isinstance(event, TaskStatusUpdateEvent):
        return False
    return (
        EPHEMERAL_STATUS_METADATA_KEY in event.metadata
        and bool(event.metadata[EPHEMERAL_STATUS_METADATA_KEY])
    )


def _require_task(task: object) -> Task:
    if not isinstance(task, Task):
        raise TypeError(f"Expected Task, got {type(task).__name__}")
    return task


def _parts_preview(parts, max_len: int) -> str:
    """Return the first non-empty text part, trimmed and truncated.

    Args:
        parts: The parts to scan, in order.
        max_len: Maximum number of characters to return before appending "...".

    Returns:
        Truncated text preview, or "<no text>" if no text content is found.
    """
    for part in parts:
        if part.text:
            text = part.text.strip()
            return text[:max_len] + ("..." if len(text) > max_len else "")
    return NO_TEXT


def extract_input_preview(message: Optional[Message], max_len: int = 120) -> str:
    """Return a short preview of an A2A message.

    Conversation content, so it belongs on DEBUG and nowhere else: an INFO
    record travels to logstash, where a user's own words have a different
    retention and a wider audience than the task store they are already kept in.
    INFO gets ``empty_input_warning`` instead. The same rule is why the
    ``sse_starlette`` logger is capped at WARNING — see ``logging.filters``.

    Args:
        message: The A2A Message to preview, or None.
        max_len: Maximum number of characters to return before appending "...".

    Returns:
        Truncated text preview, or "<no text>" if no text content is found.
    """
    return _parts_preview(message.parts, max_len) if message else NO_TEXT


def extract_event_preview(event: object, max_len: int = 120) -> str:
    """Return a short preview of what an outbound event carries.

    The counterpart of ``extract_input_preview`` for the other direction, and
    bound by the same rule: DEBUG only. Without it an agent's own words cannot
    be read back from the logs at all, since the transport logger that used to
    dump them is capped at WARNING for precisely that reason.

    Args:
        event: The event being delivered.
        max_len: Maximum number of characters to return before appending "...".

    Returns:
        Truncated text preview, or "<no text>" when the event carries no text —
        a bare transition, or an artifact chunk holding a file or a data part.
    """
    if isinstance(event, Task):
        return _parts_preview(event.status.message.parts, max_len)
    if isinstance(event, TaskStatusUpdateEvent):
        return _parts_preview(event.status.message.parts, max_len)
    if isinstance(event, TaskArtifactUpdateEvent):
        return _parts_preview(event.artifact.parts, max_len)
    if isinstance(event, Message):
        return _parts_preview(event.parts, max_len)
    return NO_TEXT


def empty_input_warning(message: Optional[Message]) -> str:
    """Flags a turn that reached the agent carrying nothing at all.

    The INFO line says nothing about the input in the normal case: the text
    itself belongs on DEBUG, and a count of it is a number nobody acts on. The
    one thing worth a word is the anomaly — an agent that was handed no parts
    cannot answer, and that reads identically to an agent that ignored the user
    unless the log says which happened.

    Args:
        message: The inbound A2A Message, or None when the turn carried none.

    Returns:
        A leading-comma fragment naming the anomaly, empty when the turn carried
        anything at all — including a file or a data part, which is not text but
        is still something the agent was given.
    """
    if message is None or not message.parts:
        return ', input=<empty>'
    return ''


def is_task_interrupted(task: Task) -> bool:
    """Return True if the task is in an interrupted state and can be resumed.

    Args:
        task: The task to check.

    Returns:
        True if task.status.state is in INTERRUPT_TASK_STATES, False otherwise.

    Raises:
        TypeError: If task is not a Task instance.
    """
    _require_task(task)
    return task.status.state in INTERRUPT_TASK_STATES


def task_history_message_ids(task: Task) -> set[str]:
    """Return the set of message_ids present in task history.

    Args:
        task: The task whose history to inspect.

    Returns:
        Set of non-empty message_id strings found in task.history.

    Raises:
        TypeError: If task is not a Task instance.
    """
    _require_task(task)
    return {m.message_id for m in task.history if m.message_id}


def is_message_in_task_history(
        task: Task,
        *,
        message: Message | None = None,
        message_id: str | None = None,
) -> bool:
    """Return True if the message is already present in task history by message_id.

    Args:
        task: The task whose history to search.
        message: The message to look up (uses message.message_id).
        message_id: The message_id string to look up directly.

    Returns:
        True if the resolved message_id is found in task.history, False otherwise.

    Raises:
        TypeError: If task is not a Task instance.
        ValueError: If neither message nor message_id is provided.
    """
    _require_task(task)
    if message is None and message_id is None:
        raise ValueError("Either message or message_id must be provided")

    m_id = message.message_id if message is not None else message_id
    return bool(m_id) and m_id in task_history_message_ids(task)
