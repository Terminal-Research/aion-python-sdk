"""Registers the suite's own extension descriptors in the server process.

Every agent package calls ``register_scenario_extensions()`` when the server
imports it. The descriptors are registered active: ``aion.yaml``'s
``enabled_extensions`` is applied to the registry before the agent module is
loaded, so an inactive descriptor added here could never be turned on, and
what these two exist to drive is the co-activation and routing rules rather
than the enabling one. ``tests/scenarios/extensions.py`` says why the suite
owns them at all.
"""

from __future__ import annotations

from typing import ClassVar

from aion.core.a2a import A2ABaseModel
from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
from aion.core.runtime import (
    ExtensionDescriptor,
    MessagesCollector,
    aion_a2a_extension_registry,
)
from pydantic import Field

from tests.scenarios.extensions import (
    SCENARIO_EVENT_EXTENSION_URI,
    SCENARIO_EVENT_PAYLOAD_SCHEMA,
    SCENARIO_EVENT_TYPE,
    SCENARIO_REQUIRES_EXTENSION_URI,
)

__all__ = ["ScenarioEventPayload", "register_scenario_extensions"]


class ScenarioEventPayload(A2ABaseModel):
    """Payload of the suite's own event kind."""

    SCHEMA_URI: ClassVar[str] = SCENARIO_EVENT_PAYLOAD_SCHEMA
    EVENT_TYPE: ClassVar[str] = SCENARIO_EVENT_TYPE

    label: str = Field(description="What this event is about, echoed back by the agent.")


def register_scenario_extensions() -> None:
    """Add the suite's two descriptors to the process-wide registry."""
    aion_a2a_extension_registry.register(
        ExtensionDescriptor(
            uri=SCENARIO_REQUIRES_EXTENSION_URI,
            requires=(DAEMON_EXTENSION_URI_V1,),
            description="Scenario-owned marker extension that requires the daemon extension.",
        )
    )
    aion_a2a_extension_registry.register(
        ExtensionDescriptor(
            uri=SCENARIO_EVENT_EXTENSION_URI,
            collector=MessagesCollector(ScenarioEventPayload),
            description="Scenario-owned event vocabulary of one kind, for the router's fallback.",
        )
    )
