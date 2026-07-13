"""Cross-package registry of ExtensionDescriptor entries and activation state."""

from __future__ import annotations

import dataclasses
from typing import Iterable

from aion.core.a2a.extensions.behaviour_evolution import (
    EvolutionDirectiveEventPayload,
    EvolutionVerdictEventPayload,
)
from aion.core.a2a.extensions.cards import CardActionEventPayload
from aion.core.a2a.extensions.daemon import DaemonExtensionPayload
from aion.core.a2a.extensions.distribution import DistributionExtensionV1
from aion.core.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageEventPayload,
    ReactionEventPayload,
)
from aion.core.a2a.extensions.traceability import TraceabilityExtensionV1
from aion.core.constants.a2a import (
    CARDS_EXTENSION_URI_V1,
    DAEMON_EXTENSION_URI_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    MESSAGING_EXTENSION_URI_V1,
    BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
    GET_CONTEXT_EXTENSION_URI_V1,
    GET_CONTEXTS_LIST_EXTENSION_URI_V1,
    TRACEABILITY_EXTENSION_URI_V1,
)
from aion.core.metaclasses import Singleton

from .descriptors import ExtensionDescriptor, MessagesCollector, TaskMetadataCollector

__all__ = [
    "AionA2AExtensionRegistry",
    "aion_a2a_extension_registry",
]


class AionA2AExtensionRegistry(metaclass=Singleton):
    """Process-wide registry of known A2A extensions and their activation state.

    Extensions self-register a descriptor here at import time, from
    whichever package owns them, each with its own default `active` value.
    activate() is called once the running agent's config is available,
    typically from AgentManager, to turn on the subset of
    registered-inactive descriptors that agent's config lists - so
    get_all() can tell a request-time verifier which registered extensions
    this particular agent actually supports, without either side needing
    to know about the other.
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, ExtensionDescriptor] = {}
        self._defaults: dict[str, tuple[bool, str | None]] = {}

    def register(self, descriptor: ExtensionDescriptor) -> None:
        """Register (or replace) an extension descriptor by URI.

        Snapshots descriptor.active/unavailable_reason as this URI's
        defaults for reset_to_default().
        """
        self._descriptors[descriptor.uri] = descriptor
        self._defaults[descriptor.uri] = (descriptor.active, descriptor.unavailable_reason)

    def activate(self, uris: Iterable[str]) -> None:
        """Turn on exactly the registered descriptors named in `uris`.

        Additive only - descriptors not named here keep whatever active
        state they already had (typically their registered default).
        There is no way to turn a descriptor off via activate(); use
        reset_to_default() to restore original defaults (see module
        docstring for why that's a test concern, not a production one).
        """
        for uri in uris:
            descriptor = self._descriptors.get(uri)
            if descriptor is not None:
                self._descriptors[uri] = dataclasses.replace(descriptor, active=True)

    def mark_unavailable(self, uri: str, reason: str) -> None:
        """Record that an enabled extension cannot function on this deployment.

        Called at startup by whichever component owns the extension's runtime
        dependencies (e.g. the server marks a task-handler extension whose
        optional toolkit is not installed). The request-time verifier rejects
        any request declaring the extension with exactly this reason. A URI
        with no registered descriptor is a no-op, mirroring activate().
        """
        descriptor = self._descriptors.get(uri)
        if descriptor is not None:
            self._descriptors[uri] = dataclasses.replace(descriptor, unavailable_reason=reason)

    def reset_to_default(self) -> None:
        """Restore every descriptor's activation/availability to registration values."""
        for uri, (active, unavailable_reason) in self._defaults.items():
            self._descriptors[uri] = dataclasses.replace(
                self._descriptors[uri],
                active=active,
                unavailable_reason=unavailable_reason,
            )

    def get_all(self) -> tuple[ExtensionDescriptor, ...]:
        """Return every registered descriptor with its current activation state."""
        return tuple(self._descriptors.values())

    def __repr__(self) -> str:
        return (
            f"AionA2AExtensionRegistry(descriptors="
            f"{[(uri, d.active) for uri, d in self._descriptors.items()]})"
        )


aion_a2a_extension_registry = AionA2AExtensionRegistry()

aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=DAEMON_EXTENSION_URI_V1,
        collector=TaskMetadataCollector(DaemonExtensionPayload),
        description="Daemon-scoped invocation context: daemon identity, behavior, and environment.",
        active=False,
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=TRACEABILITY_EXTENSION_URI_V1,
        collector=TaskMetadataCollector(TraceabilityExtensionV1),
        description="W3C trace context propagation for distributed tracing.",
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=DISTRIBUTION_EXTENSION_URI_V1,
        collector=TaskMetadataCollector(DistributionExtensionV1),
        description="Distribution-scoped invocation context: channel identity, behavior, and environment.",
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=MESSAGING_EXTENSION_URI_V1,
        collector=MessagesCollector(
            MessageEventPayload,
            ReactionEventPayload,
            CommandEventPayload,
        ),
        description="Messaging event types and payload schemas layered on the distribution "
                    "extension; delivered per-message-part via the event extension as an "
                    "event envelope, not as a single aggregate payload.",
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=EVENT_EXTENSION_URI_V1,
        description="Generic CloudEvents-style envelope for per-message-part events; "
                    "concrete payload vocabularies (e.g. messaging) are layered on top.",
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=CARDS_EXTENSION_URI_V1,
        collector=MessagesCollector(CardActionEventPayload),
        description="JSX-like card rendering for the distribution extension.",
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1,
        requires=(DAEMON_EXTENSION_URI_V1,),
        collector=MessagesCollector(
            EvolutionDirectiveEventPayload,
            EvolutionVerdictEventPayload,
        ),
        description="Self-improvement flow: daemon-driven directive/verdict/result routing.",
        active=False,
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=GET_CONTEXT_EXTENSION_URI_V1,
        description="Get conversation info based on context.",
    )
)
aion_a2a_extension_registry.register(
    ExtensionDescriptor(
        uri=GET_CONTEXTS_LIST_EXTENSION_URI_V1,
        description="Get list of available contexts.",
    )
)
