"""What a client needs to talk to the scripted executor; imports nothing from the SDK.

The executor's turn is scripted by an event of its own. The stand-in
evolution toolkit is scripted with the same fields, carried by the evolution
directive's event.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "EVOLUTION_DIRECTIVE_EVENT_TYPE",
    "EVOLUTION_DIRECTIVE_SCHEMA",
    "EVOLUTION_URI",
    "FAKE_TOOLKIT",
    "SCRIPT_EVENT_TYPE",
    "SCRIPT_SCHEMA",
    "URI",
    "script_contract",
]

URI = "https://example.invalid/extensions/scripted/1.0.0"
"""The extension URI the scripted executor serves."""

SCRIPT_EVENT_TYPE = "invalid.example.extensions.scripted.script.1.0.0"
"""The Event type of the message that scripts a turn."""

SCRIPT_SCHEMA = f"{URI}#ScriptPayload"
"""The schema of the script's data part."""

EVOLUTION_URI = "https://docs.aion.to/a2a/extensions/aion/behavior/evolution/1.0.0"
"""The evolution extension, which the stand-in toolkit serves through the SDK's wrapper."""

EVOLUTION_DIRECTIVE_EVENT_TYPE = "to.aion.behavior.evolution.directive.1.0.0"
"""The Event type of the evolution directive, which carries the stand-in toolkit's script."""

EVOLUTION_DIRECTIVE_SCHEMA = f"{EVOLUTION_URI}#EvolutionDirectiveEventPayload"
"""The schema of the evolution directive's data part."""

FAKE_TOOLKIT = Path(__file__).resolve().parent / "fake_toolkit"
"""The directory that holds the stand-in evolution toolkit's module."""


def script_contract(uri: str) -> tuple[str, str]:
    """The Event type and the data part schema that script a turn of the extension at ``uri``."""
    if uri == EVOLUTION_URI:
        return EVOLUTION_DIRECTIVE_EVENT_TYPE, EVOLUTION_DIRECTIVE_SCHEMA
    return SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA
