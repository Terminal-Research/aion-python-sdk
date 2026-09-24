"""One behaviour per command, grouped by the tag its scenarios carry.

``BEHAVIORS`` is the whole of the routing table. A command that the registry
declares but no behaviour implements answers ``not implemented``, so the gap
shows up as a failing contract scenario rather than as a wrong menu.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from google.adk.events import Event

from tests.scenarios.agents.adk_core.invocation import Invocation

from .cards import card
from .config import config, event, ext, whoami
from .errors import fail
from .events import ids, steps
from .files import artifacts, parts
from .lifecycle import slow
from .outbox import outbox_message, outbox_task
from .payload import big
from .smoke import echo, help_menu
from .streaming import stream, typing_indicator

__all__ = ["BEHAVIORS", "Behavior"]

# A behaviour either answers through the thread and returns nothing, or
# returns the ADK event the agent must yield on its behalf.
Behavior = Callable[[Invocation], Awaitable[Optional[Event]]]

BEHAVIORS: dict[str, Behavior] = {
    "help": help_menu,
    "echo": echo,
    "stream": stream,
    "typing": typing_indicator,
    "steps": steps,
    "slow": slow,
    "ids": ids,
    "parts": parts,
    "artifacts": artifacts,
    "card": card,
    "big": big,
    "fail": fail,
    "ext": ext,
    "whoami": whoami,
    "event": event,
    "config": config,
    "outbox-message": outbox_message,
    "outbox-task": outbox_task,
}
