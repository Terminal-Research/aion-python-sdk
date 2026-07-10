from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional

from .descriptors import ExtensionActivationError, ExtensionDescriptor

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

__all__ = ["AionRuntimeExtensions"]


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

    A URI with no matching descriptor at all is silently skipped.

    Raises:
        ExtensionActivationError: an active extension is not active for
            the current agent, is missing a required co-activated
            extension, or its collector raises during payload collection.
    """
    descriptors = tuple(descriptors)
    enabled_uris = frozenset(d.uri for d in descriptors if d.uri in active_uris and d.active)

    verified: dict[str, Any] = {}
    for descriptor in descriptors:
        if descriptor.uri not in active_uris:
            continue

        if not descriptor.active:
            raise ExtensionActivationError(
                descriptor.uri, reason="extension is not active for this agent"
            )

        missing = frozenset(descriptor.requires) - enabled_uris
        if missing:
            raise ExtensionActivationError(descriptor.uri, missing_requires=missing)

        verified[descriptor.uri] = descriptor.collector.collect(descriptor.uri, request_context)

    return verified


class AionRuntimeExtensions:
    """Verified, per-request set of active, registered extensions.

    Built once via `AionRuntimeExtensions.collect()` and then handed to
    everything downstream (routing, is_active() checks, typed payload
    access) as a single object. Only extensions with a registered,
    currently-active ExtensionDescriptor appear here.

    For TaskMetadataCollector extensions (daemon, traceability, distribution),
    get(uri) returns the parsed payload model. For MessagesCollector extensions
    (messaging, cards), get(uri) returns a typed Event when an event envelope
    was present in the request, or None for direct A2A requests.
    """

    def __init__(self, verified: dict[str, Any]) -> None:
        self._verified = verified

    @classmethod
    def collect(
        cls,
        request_context: "RequestContext",
        descriptors: Iterable[ExtensionDescriptor],
    ) -> "AionRuntimeExtensions":
        """Collect then verify in one step - the entry point builders use."""
        active_uris = _collect(request_context)
        return cls(_verify(active_uris, request_context, descriptors))

    def is_active(self, *uris: str) -> bool:
        """Return whether every given, registered extension URI is active.

        An unregistered URI (no ExtensionDescriptor) always reads as
        inactive here, even if the client declared it.
        """
        if not uris:
            return True
        return all(uri in self._verified for uri in uris)

    def get(self, uri: str) -> Optional[Any]:
        """Return the verified payload for uri, or None when inactive or a marker extension."""
        return self._verified.get(uri)

    def __contains__(self, uri: str) -> bool:
        return uri in self._verified

    def __iter__(self):
        return iter(self._verified)

    def __bool__(self) -> bool:
        return bool(self._verified)

    def first_event(self) -> Optional[Any]:
        """Return the first Event found among verified extension payloads, or None.

        Used by AionRuntimeContextBuilder to populate AionRuntimeContext.event
        from whichever MessagesCollector extension extracted the event.
        """
        from aion.core.runtime.context.models import Event
        return next((v for v in self._verified.values() if isinstance(v, Event)), None)

    def __repr__(self) -> str:
        return f"AionRuntimeExtensions({sorted(self._verified)})"
