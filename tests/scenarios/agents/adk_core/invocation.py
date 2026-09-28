"""What a behaviour is allowed to know about the turn it answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aion.adk.authoring.invocation import Thread
from aion.core.runtime import AionRuntimeContext

from tests.scenarios.commands import ParsedCommand

__all__ = ["Invocation"]


@dataclass(frozen=True)
class Invocation:
    """The turn a behaviour runs in.

    Attributes:
        thread: Thread bound to this invocation; everything the caller sees
            is spoken through it.
        context: Aion runtime context behind the turn.
        command: The inbound text, split into command key and argument.
    """

    thread: Thread
    context: AionRuntimeContext
    command: ParsedCommand

    @property
    def task_id(self) -> Optional[str]:
        """Task this turn belongs to, as the inbox reports it."""
        inbox = self.context.inbox
        if inbox is None:
            return None
        if inbox.task is not None and inbox.task.id:
            return inbox.task.id
        if inbox.message is not None and inbox.message.task_id:
            return inbox.message.task_id
        return None

    @property
    def context_id(self) -> Optional[str]:
        """Context this turn belongs to, as the inbox reports it."""
        inbox = self.context.inbox
        if inbox is None:
            return None
        if inbox.message is not None and inbox.message.context_id:
            return inbox.message.context_id
        if inbox.task is not None and inbox.task.context_id:
            return inbox.task.context_id
        return None
