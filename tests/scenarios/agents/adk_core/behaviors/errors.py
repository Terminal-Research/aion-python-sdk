"""Failing on purpose: crashing at the two points an agent can crash at, or saying why."""

from __future__ import annotations

from aion.core.exceptions import TaskFailedError

from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.commands import FAIL_MESSAGE, FAIL_MODES, FAIL_REASON_TEXT, FAIL_REPLY_TEXT

__all__ = ["fail"]


async def fail(invocation: Invocation) -> None:
    """Raise, with or without having answered first.

    `exception` raises before saying anything, so the turn produced no
    content at all. `after-reply` answers and then raises, which is the case
    where a client already holds something the task is about to disown.
    `reason` fails the task on purpose, with a reason the caller reads.
    """
    mode = invocation.command.argument.strip() or FAIL_MODES[0]
    if mode == "reason":
        raise TaskFailedError(FAIL_REASON_TEXT)
    if mode == "after-reply":
        await invocation.thread.reply(FAIL_REPLY_TEXT)
    raise RuntimeError(f"{FAIL_MESSAGE}: {mode}")
