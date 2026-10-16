"""What a client needs to talk to the scripted toolkit; imports nothing from the SDK.

The toolkit is installed twice, under two URIs: as it is, and as an
``exclusive_per_context`` toolkit that runs one task per context. Each has
its own script event, so a request activates exactly one of them.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "ENTRY_POINT",
    "EXCLUSIVE_ENTRY_POINT",
    "EXCLUSIVE_SCRIPT_EVENT_TYPE",
    "EXCLUSIVE_SCRIPT_SCHEMA",
    "EXCLUSIVE_URI",
    "SCRIPT_EVENT_TYPE",
    "SCRIPT_SCHEMA",
    "SITE",
    "URI",
    "script_contract",
]

URI = "https://example.invalid/toolkits/scripted/1.0.0"
"""The extension URI the toolkit serves."""

SCRIPT_EVENT_TYPE = "invalid.example.toolkits.scripted.script.1.0.0"
"""The Event type of the message that scripts a turn."""

SCRIPT_SCHEMA = f"{URI}#ScriptPayload"
"""The schema of the script's data part."""

ENTRY_POINT = "scripted"
"""The entry point's name in the ``aion.toolkits`` group."""

EXCLUSIVE_URI = "https://example.invalid/toolkits/scripted-exclusive/1.0.0"
"""The extension URI of the toolkit that runs one task per context."""

EXCLUSIVE_SCRIPT_EVENT_TYPE = "invalid.example.toolkits.scripted-exclusive.script.1.0.0"
"""The Event type of the message that scripts a turn of the exclusive toolkit."""

EXCLUSIVE_SCRIPT_SCHEMA = f"{EXCLUSIVE_URI}#ScriptPayload"
"""The schema of the exclusive toolkit's script data part."""

EXCLUSIVE_ENTRY_POINT = "scripted-exclusive"
"""The exclusive toolkit's entry point name in the ``aion.toolkits`` group."""

SITE = Path(__file__).resolve().parent / "site"
"""The directory whose distribution metadata names the entry points."""


def script_contract(uri: str) -> tuple[str, str]:
    """The Event type and the data part schema that script a turn of the toolkit serving ``uri``."""
    if uri == EXCLUSIVE_URI:
        return EXCLUSIVE_SCRIPT_EVENT_TYPE, EXCLUSIVE_SCRIPT_SCHEMA
    return SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA
