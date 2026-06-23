"""Utility functions for inspecting A2A task and message objects."""

from typing import Optional

from a2a.types import Message, Task

from aion.server.a2a.constants import INTERRUPT_TASK_STATES

__all__ = [
    "is_task_interrupted",
    "task_history_message_ids",
    "is_message_in_task_history",
    "extract_input_preview",
]


def _require_task(task: object) -> Task:
    if not isinstance(task, Task):
        raise TypeError(f"Expected Task, got {type(task).__name__}")
    return task


def extract_input_preview(message: Optional[Message], max_len: int = 120) -> str:
    """Return a short preview of an A2A message for logging purposes.

    Scans message parts in order and returns the first non-empty text part,
    stripped of leading/trailing whitespace and truncated to max_len characters.
    Returns "<no text>" if the message is None, has no parts, or contains no text parts.

    Args:
        message: The A2A Message to preview, or None.
        max_len: Maximum number of characters to return before appending "...".

    Returns:
        Truncated text preview, or "<no text>" if no text content is found.
    """
    if message:
        for part in message.parts:
            if part.text:
                text = part.text.strip()
                return text[:max_len] + ("..." if len(text) > max_len else "")
    return "<no text>"


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
