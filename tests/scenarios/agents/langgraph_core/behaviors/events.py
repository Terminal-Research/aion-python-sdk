"""What the caller sees of a turn: its statuses, its identity and a reaction."""

from __future__ import annotations

from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.commands import REACTION_KEY, ids_text, react_text, steps_texts

__all__ = ["steps", "ids", "react"]

DEFAULT_STEPS = 3


async def steps(invocation: Invocation) -> None:
    """Report progress ``n`` times, then let the turn complete."""
    count = invocation.command.int_argument(DEFAULT_STEPS)
    for text in steps_texts(count):
        await invocation.thread.reply(text)


async def ids(invocation: Invocation) -> None:
    """Answer with the task and context ids this turn runs under."""
    await invocation.thread.reply(ids_text(invocation.task_id, invocation.context_id))


async def react(invocation: Invocation) -> None:
    """React to the message the turn's message event names, then reply."""
    if invocation.thread.message is not None:
        await invocation.thread.message.react(REACTION_KEY)
    await invocation.thread.reply(react_text())
