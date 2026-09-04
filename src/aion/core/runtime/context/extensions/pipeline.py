from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional

from .descriptors import ExtensionActivationError, ExtensionDescriptor

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

__all__ = ["AionRuntimeExtensions", "UnknownExtension"]


@dataclass(frozen=True)
class UnknownExtension:
    """An extension URI declared on the request that no registered descriptor claims.

    The agent doesn't know how to handle it, so it is neither verified nor
    routed nor considered active - but it is carried on the runtime context
    for visibility (logging, forwarding, diagnostics) instead of being
    silently dropped at parsing.
    """

    uri: str


def _collect(request_context: "RequestContext") -> frozenset[str]:
    """Return every URI the request declares active, from any signal.

    An extension counts as active when it was activated via the
    A2A-Extensions service parameter (requested_extensions header), when it
    appears in the message's own extensions[] list, or when it has an entry
    in request-level metadata (e.g. distribution/daemon).
    """
    message = request_context.message
    message_uris = frozenset(message.extensions or ()) if message is not None else frozenset()
    metadata = dict(request_context.metadata) if request_context.metadata else {}
    requested = frozenset(request_context.requested_extensions or ())
    return requested | message_uris | frozenset(metadata.keys())


def _verify(
    active_uris: frozenset[str],
    request_context: "RequestContext",
    descriptors: Iterable[ExtensionDescriptor],
) -> dict[str, Any]:
    """Verify each declared-active URI with a matching descriptor.

    A URI with no matching descriptor at all is not verified here - the
    caller carries it as an UnknownExtension instead.

    Raises:
        ExtensionActivationError: an active extension is not supported by
            the current agent, is enabled but unavailable on this
            deployment, is missing a required co-activated extension, or
            its collector raises during payload collection.
    """
    descriptors = tuple(descriptors)
    enabled_uris = frozenset(d.uri for d in descriptors if d.uri in active_uris and d.active)

    verified: dict[str, Any] = {}
    for descriptor in descriptors:
        if descriptor.uri not in active_uris:
            continue

        if not descriptor.active:
            raise ExtensionActivationError(
                descriptor.uri,
                reason="the extension is not enabled for this agent - add its "
                       "URI to enabled_extensions in the agent's aion.yaml to "
                       "allow it",
            )

        if descriptor.unavailable_reason:
            # Enabled by the agent, but its runtime dependencies are missing
            # on this deployment - reject with the extension-authored reason
            # recorded via AionA2AExtensionRegistry.mark_unavailable().
            raise ExtensionActivationError(
                descriptor.uri, reason=descriptor.unavailable_reason
            )

        missing = frozenset(descriptor.requires) - enabled_uris
        if missing:
            raise ExtensionActivationError(descriptor.uri, missing_requires=missing)

        verified[descriptor.uri] = descriptor.collector.collect(descriptor.uri, request_context)

    return verified


class AionRuntimeExtensions:
    """Per-request set of extensions declared on the inbound request.

    Built once via `AionRuntimeExtensions.collect()` and then handed to
    everything downstream (routing, is_active() checks, typed payload
    access) as a single object. Extensions with a registered,
    currently-active ExtensionDescriptor are verified and expose their
    payload; declared URIs no descriptor claims are carried as inert
    UnknownExtension entries - visible via get()/iteration but never
    active and never routed.

    For TaskMetadataCollector extensions (daemon, traceability, distribution),
    get(uri) returns the parsed payload model. For MessagesCollector extensions
    (messaging, cards), get(uri) returns a typed Event when an event envelope
    was present in the request, or None for direct A2A requests.
    """

    def __init__(
        self,
        verified: dict[str, Any],
        unknown: Iterable[str] = (),
    ) -> None:
        self._verified = verified
        self._unknown = {uri: UnknownExtension(uri) for uri in unknown}

    @classmethod
    def collect(
        cls,
        request_context: "RequestContext",
        descriptors: Iterable[ExtensionDescriptor],
    ) -> "AionRuntimeExtensions":
        """Collect then verify in one step - the entry point builders use."""
        active_uris = _collect(request_context)
        descriptors = tuple(descriptors)
        verified = _verify(active_uris, request_context, descriptors)
        known_uris = frozenset(d.uri for d in descriptors)
        return cls(verified, unknown=active_uris - known_uris)

    @property
    def unknown(self) -> tuple[UnknownExtension, ...]:
        """Declared URIs no registered descriptor claims - carried, not handled."""
        return tuple(self._unknown.values())

    def is_active(self, *uris: str) -> bool:
        """Return whether every given, registered extension URI is active.

        An unregistered URI (no ExtensionDescriptor) always reads as
        inactive here, even though the declaration is still carried in
        `unknown` - active means "verified and handleable", and the agent
        has no handling for it.
        """
        if not uris:
            return True
        return all(uri in self._verified for uri in uris)

    def get(self, uri: str) -> Optional[Any]:
        """Return the verified payload for uri, an UnknownExtension for a
        declared-but-unregistered uri, or None when not declared (also None
        for declared marker extensions, which have no payload)."""
        if uri in self._verified:
            return self._verified[uri]
        return self._unknown.get(uri)

    def __contains__(self, uri: str) -> bool:
        return uri in self._verified or uri in self._unknown

    def __iter__(self):
        yield from self._verified
        yield from self._unknown

    def __bool__(self) -> bool:
        return bool(self._verified) or bool(self._unknown)

    def first_event(self) -> Optional[Any]:
        """Return the first Event found among verified extension payloads, or None.

        Used by AionRuntimeContextBuilder to populate AionRuntimeContext.event
        from whichever MessagesCollector extension extracted the event.
        """
        from aion.core.runtime.context.models import Event
        return next((v for v in self._verified.values() if isinstance(v, Event)), None)

    def __repr__(self) -> str:
        return f"AionRuntimeExtensions({sorted(self._verified)})"
