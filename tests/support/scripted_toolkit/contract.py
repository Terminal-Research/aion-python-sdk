"""What a client needs to talk to the scripted toolkit; imports nothing from the SDK."""

from __future__ import annotations

from pathlib import Path

__all__ = ["ENTRY_POINT", "SCRIPT_EVENT_TYPE", "SCRIPT_SCHEMA", "SITE", "URI"]

URI = "https://example.invalid/toolkits/scripted/1.0.0"
"""The extension URI the toolkit serves."""

SCRIPT_EVENT_TYPE = "invalid.example.toolkits.scripted.script.1.0.0"
"""The Event type of the message that scripts a turn."""

SCRIPT_SCHEMA = f"{URI}#ScriptPayload"
"""The schema of the script's data part."""

ENTRY_POINT = "scripted"
"""The entry point's name in the ``aion.toolkits`` group."""

SITE = Path(__file__).resolve().parent / "site"
"""The directory whose distribution metadata names the entry point."""
