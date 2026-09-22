"""One behaviour per command, grouped by the tag its scenarios carry.

``BEHAVIORS`` is the whole of the routing table. A command that the registry
declares but no behaviour implements answers ``not implemented``, so the gap
shows up as a failing contract scenario rather than as a wrong menu.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from tests.scenarios.agents.langgraph_core.invocation import Invocation

from .config import config, event, ext, whoami
from .errors import fail
from .events import ids, steps
from .files import artifacts, parts
from .interrupts import ask, ask_twice
from .lifecycle import slow
from .outbox import outbox_message, outbox_task
from .smoke import echo, help_menu
from .streaming import stream

__all__ = ["BEHAVIORS", "Behavior"]

Behavior = Callable[[Invocation], Awaitable[Optional[dict]]]

BEHAVIORS: dict[str, Behavior] = {
    "help": help_menu,
    "echo": echo,
    "stream": stream,
    "steps": steps,
    "slow": slow,
    "ask": ask,
    "ask-twice": ask_twice,
    "ids": ids,
    "parts": parts,
    "artifacts": artifacts,
    "fail": fail,
    "ext": ext,
    "whoami": whoami,
    "event": event,
    "config": config,
    "outbox-message": outbox_message,
    "outbox-task": outbox_task,
}
