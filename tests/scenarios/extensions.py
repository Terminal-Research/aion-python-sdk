"""Extension URIs the scenarios declare, including two the suite owns itself.

Read by the tests and by the agents, like ``commands.py``: the tests declare
these URIs on a request, and the agents register the descriptors behind them
so the server's own extension pipeline has something to verify.

Two of the SDK's rules have no production extension that can demonstrate them
on a deployment a scenario can start. ``requires`` is declared today only by
behaviour evolution, whose optional toolkit decides at startup whether the
extension is available at all - so a scenario built on it would be asserting
the toolkit's presence, not the co-activation rule. The router's ``on_event``
fallback needs an event kind that no kind-specific handler claims, and every
kind the core vocabularies define has one.

So the suite registers its own: a marker extension that requires the daemon
extension, and an event vocabulary of one kind. Both are ordinary
``ExtensionDescriptor`` registrations - the pipeline under test is the SDK's,
and these are only a deterministic way in.
"""

from __future__ import annotations

__all__ = [
    "SCENARIO_REQUIRES_EXTENSION_URI",
    "SCENARIO_EVENT_EXTENSION_URI",
    "SCENARIO_EVENT_TYPE",
    "SCENARIO_EVENT_PAYLOAD_SCHEMA",
]

SCENARIO_REQUIRES_EXTENSION_URI = "https://example.invalid/extensions/scenario-requires/1.0.0"
"""Marker extension whose descriptor requires the daemon extension alongside it."""

SCENARIO_EVENT_EXTENSION_URI = "https://example.invalid/extensions/scenario-event/1.0.0"
"""Event vocabulary of one kind, which no router handler claims by name."""

SCENARIO_EVENT_TYPE = "invalid.example.scenario.1.0.0"
"""CloudEvents ``type`` of that one kind."""

SCENARIO_EVENT_PAYLOAD_SCHEMA = f"{SCENARIO_EVENT_EXTENSION_URI}#ScenarioEventPayload"
"""Schema its payload part is tagged with."""
