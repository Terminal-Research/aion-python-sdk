"""Availability contract shared by the handler protocol and its implementations.

Lives in its own module (not base.py) because base.py imports the concrete
handler subpackages for discovery, and those handlers need this type - a
shared leaf module keeps the import graph acyclic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["ExtensionAvailability"]


@dataclass(frozen=True)
class ExtensionAvailability:
    """Result of a handler's availability check for the current agent.

    When unavailable, `reason` is the handler-authored, user-facing message a
    request declaring the extension is rejected with - each extension states
    exactly why it can't run here (e.g. which optional toolkit is missing),
    instead of the routing layer guessing a generic cause.
    """

    available: bool
    reason: Optional[str] = None

    @classmethod
    def ok(cls) -> "ExtensionAvailability":
        return cls(available=True)

    @classmethod
    def unavailable(cls, reason: str) -> "ExtensionAvailability":
        return cls(available=False, reason=reason)
