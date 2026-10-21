"""Cross-package registry of ExtensionDescriptor entries and activation state.

Nothing is registered at import. The server fills the registry while it
builds the application: :func:`register_protocol_extensions` registers the
protocol extensions defined here, the server adds its executable extensions
and activates what the agent's ``aion.yaml`` enables. Code that reads the
registry outside a server (a test, a tool) calls
:func:`register_protocol_extensions` itself.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable

from aion.core.a2a.extensions.cards import CardActionEventPayload
from aion.core.a2a.extensions.cron import CronExtensionV1
from aion.core.a2a.extensions.daemon import DaemonExtensionPayload
from aion.core.a2a.extensions.distribution import DistributionExtensionV1
from aion.core.a2a.extensions.messaging import (
    CommandEventPayload,
    MessageEventPayload,
    ReactionEventPayload,
)
from aion.core.a2a.extensions.traceability import TraceabilityExtensionV1
from aion.core.constants.a2a import (
    AION_USAGE_ATTRIBUTION_HEADER,
    CARDS_EXTENSION_URI_V1,
    CRON_EXTENSION_URI_V1,
    WELCOME_MESSAGE_EXTENSION_URI_V1,
    DAEMON_EXTENSION_URI_V1,
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
    CONTEXT_EXTENSION_URI_V1,
    MESSAGING_EXTENSION_URI_V1,
    TRACEABILITY_EXTENSION_URI_V1,
    USAGE_ATTRIBUTION_EXTENSION_URI_V1,
)
from aion.core.metaclasses import Singleton

from .descriptors import (
    ExtensionDescriptor,
    HeaderCollector,
    MessagesCollector,
    TaskMetadataCollector,
    WelcomeMessageCollector,
)

__all__ = [
    "AionA2AExtensionRegistry",
    "aion_a2a_extension_registry",
    "register_protocol_extensions",
]


class AionA2AExtensionRegistry(metaclass=Singleton):
    """Process-wide registry of known A2A extensions and their activation state.

    The server registers every descriptor while it builds the application,
    each with its own default `active` value: the protocol extensions
    (register_protocol_extensions) and its executable extensions. It then
    calls activate() with the URIs the agent's config lists, to turn on the
    subset of registered-inactive descriptors that agent enables - so
    get_all() can tell a request-time verifier which registered extensions
    this particular agent actually supports, without either side needing
    to know about the other.

    Three independent properties, and the registry keeps them apart:

    - **known/supported** - a descriptor is registered here at all, so the
      server recognizes the URI and can collect and verify its payload.
    - **active** - the extension is enabled for the agent now running,
      either by its registered default or via
      AgentConfig.enabled_extensions.
    - **advertised** - the extension may be published on the generated
      AgentCard.

    The card reads get_advertised(); everything request-time reads
    get_all(). That separation is what lets an extension be fully supported
    and callable while staying off the card - see
    docs/development/extension-exposure.md.
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

    def clear(self) -> None:
        """Forget every registered descriptor, before a server registers its own afresh."""
        self._descriptors.clear()
        self._defaults.clear()

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
        dependencies. The request-time verifier rejects
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
        """Return every registered descriptor with its current activation state.

        The request-time view: routing, payload collection and declaration
        verification all read this, so a descriptor that is not advertised
        is verified and handled exactly like one that is.
        """
        return tuple(self._descriptors.values())

    def _servable_uris(self) -> frozenset[str]:
        """URIs whose deployment prerequisites are currently satisfied.

        Enabled for this agent, able to run on this deployment, and with
        every extension they require in that same position. A requirement
        chain is only as strong as its weakest link, so the set is narrowed
        until it stops shrinking rather than checked one level deep.

        Prerequisites, not a promise about any request: this asks what the
        deployment has in place, never what a particular request declared.
        _verify() asks that second question of an extension declared on a
        message - its requirements must be declared on the same request, not
        merely be active here - so a URI in this set can still be refused
        there.
        """
        servable = {
            uri
            for uri, descriptor in self._descriptors.items()
            if descriptor.active and descriptor.unavailable_reason is None
        }
        while True:
            narrowed = {
                uri
                for uri in servable
                if all(required in servable for required in self._descriptors[uri].requires)
            }
            if narrowed == servable:
                return frozenset(servable)
            servable = narrowed

    def unservable_reason(self, uri: str) -> str | None:
        """Why a call on this extension cannot be honored now, or None.

        The state half of the question an extension URI answers - registered
        is a wiring fact, serviceable is a deployment one, and the two change
        on completely different clocks.

        Deployment-level readiness: the descriptor is registered, enabled for
        this agent, able to run here, and everything in its requirement chain
        is active and available too. That is the whole question for a method
        extension invoked directly, which carries no extension declaration of
        its own. An extension declared on a message is held to this and then
        to one more thing in _verify(): the extensions it requires must be
        declared on that same request, so a URI this method calls serviceable
        can still be refused there for a per-request co-activation this
        cannot see.

        `advertised` is not consulted: withholding an extension from the
        Agent Card decides what a client is told, never what it may call.
        """
        descriptor = self._descriptors.get(uri)
        if descriptor is None:
            return "the extension is not registered on this server"
        if not descriptor.active:
            return (
                "the extension is not enabled for this agent - add its URI to "
                "enabled_extensions in the agent's aion.yaml to allow it"
            )
        if descriptor.unavailable_reason:
            return descriptor.unavailable_reason
        servable = self._servable_uris()
        if uri not in servable:
            missing = sorted(frozenset(descriptor.requires) - servable)
            return (
                f"the extension requires extension(s) that are not available "
                f"on this agent: {missing}"
            )
        return None

    def get_advertised(self) -> tuple[ExtensionDescriptor, ...]:
        """Return the descriptors the generated AgentCard may publish.

        The card's whole view of the registry, so the rule lives here rather
        than in the card builder: an extension is published when it may be
        advertised at all and its deployment prerequisites are satisfied.
        Withholding what the deployment cannot run - inactive, unavailable,
        or missing an extension it requires - keeps the card from offering a
        call this server could never answer.

        That is a floor, not a guarantee: a published extension has what the
        deployment owes it, and a request declaring it can still be refused
        by _verify() for a per-request co-activation this cannot see.
        Behavior evolution, which requires the daemon extension, is the case
        that makes the prerequisite check matter: enabling it without the daemon extension
        leaves every request declaring it refused, so the card must not offer
        it.
        """
        servable = self._servable_uris()
        return tuple(
            descriptor
            for uri, descriptor in self._descriptors.items()
            if uri in servable and descriptor.advertised
        )

    def __repr__(self) -> str:
        return (
            f"AionA2AExtensionRegistry(descriptors="
            f"{[(uri, d.active, d.advertised) for uri, d in self._descriptors.items()]})"
        )


aion_a2a_extension_registry = AionA2AExtensionRegistry()


def register_protocol_extensions(registry: AionA2AExtensionRegistry) -> None:
    """Register the protocol extensions the SDK defines, each with its default activation.

    Args:
        registry: The registry to register them in; the server passes
            ``aion_a2a_extension_registry``.
    """
    # The Context extension: GetContexts, GetContext and DeleteContext, all under
    # one URI. Declarative - its methods are invoked directly and need no
    # activation - and advertised, because the standard server implements the
    # whole contract. Whatever component finds the contract unfulfillable on a
    # deployment marks it unavailable, which also takes it off the AgentCard.
    registry.register(
        ExtensionDescriptor(
            uri=CONTEXT_EXTENSION_URI_V1,
            description="List, retrieve, and delete caller-visible conversation contexts.",
        )
    )

    registry.register(
        ExtensionDescriptor(
            uri=CRON_EXTENSION_URI_V1,
            collector=TaskMetadataCollector(CronExtensionV1),
            description="Cron invocation timing: intended firing and producer dispatch time.",
        )
    )

    registry.register(
        ExtensionDescriptor(
            uri=USAGE_ATTRIBUTION_EXTENSION_URI_V1,
            collector=HeaderCollector(AION_USAGE_ATTRIBUTION_HEADER),
            description=(
                "Opaque Aion-issued usage attribution propagated across managed "
                "runtime calls."
            ),
        )
    )

    registry.register(
        ExtensionDescriptor(
            uri=DAEMON_EXTENSION_URI_V1,
            collector=TaskMetadataCollector(DaemonExtensionPayload),
            description="Daemon-scoped invocation context: daemon identity, behavior, and environment.",
            active=False,
        )
    )
    registry.register(
        ExtensionDescriptor(
            uri=TRACEABILITY_EXTENSION_URI_V1,
            collector=TaskMetadataCollector(TraceabilityExtensionV1),
            description="W3C trace context propagation for distributed tracing.",
        )
    )
    registry.register(
        ExtensionDescriptor(
            uri=DISTRIBUTION_EXTENSION_URI_V1,
            collector=TaskMetadataCollector(DistributionExtensionV1),
            description="Distribution-scoped invocation context: channel identity, behavior, and environment.",
        )
    )
    registry.register(
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
    registry.register(
        ExtensionDescriptor(
            uri=EVENT_EXTENSION_URI_V1,
            description="Generic CloudEvents-style envelope for per-message-part events; "
                        "concrete payload vocabularies (e.g. messaging) are layered on top.",
        )
    )
    registry.register(
        ExtensionDescriptor(
            uri=CARDS_EXTENSION_URI_V1,
            collector=MessagesCollector(CardActionEventPayload),
            description="JSX-like card rendering for the distribution extension.",
        )
    )

    # Enabling this declaration is the agent implementer's commitment to handle it.
    registry.register(
        ExtensionDescriptor(
            uri=WELCOME_MESSAGE_EXTENSION_URI_V1,
            collector=WelcomeMessageCollector(),
            description="Generate an opening message for a new conversation.",
            active=False,
        )
    )
