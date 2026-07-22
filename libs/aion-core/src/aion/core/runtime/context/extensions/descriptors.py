from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, Type

from aion.core.a2a import A2ABaseModel
from aion.core.a2a.extensions.event import EventMessageMetadataV1, EventPartMetadataV1
from aion.core.a2a.extensions.messaging import SourceSystemEventPayload
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1, SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1
from aion.core.utils.protobuf import proto_to_dict

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from aion.core.runtime.context.models import Event, NormalizedPayload

__all__ = [
    "ExtensionActivationError",
    "ExtensionPayloadCollector",
    "MarkerCollector",
    "TaskMetadataCollector",
    "MessagesCollector",
    "ExtensionDescriptor",
]


class ExtensionActivationError(Exception):
    """An active A2A extension is malformed or missing a required co-activated extension.

    Raised during data preparation (AionRuntimeContextBuilder), before the
    request reaches routing or any handler/agent business logic.
    """

    def __init__(
        self,
        uri: str,
        *,
        missing_requires: frozenset[str] = frozenset(),
        reason: Optional[str] = None,
    ) -> None:
        self.uri = uri
        self.missing_requires = missing_requires
        self.reason = reason
        if missing_requires:
            message = (
                f"Extension '{uri}' requires extension(s) that are not active "
                f"on this request: {sorted(missing_requires)} - declare them "
                f"alongside it"
            )
        else:
            message = f"Extension '{uri}' cannot be activated: {reason}"
        super().__init__(message)


class ExtensionPayloadCollector(Protocol):
    """Interface for collecting and verifying an extension's payload from a request.

    Implementations are called once per verified, active extension during
    _verify(). The return value is stored in AionRuntimeExtensions and exposed
    via AionRuntimeExtensions.get(uri).
    """

    def collect(self, uri: str, request_context: "RequestContext") -> Any:
        """Return the parsed payload for this extension, or None.

        Args:
            uri: The extension URI being verified.
            request_context: Full inbound A2A request context.

        Returns:
            Parsed payload model instance, Event for message-parts extensions,
            or None for marker extensions.

        Raises:
            ExtensionActivationError: payload is missing or fails validation.
        """
        ...


class MarkerCollector:
    """Collector for extensions with no payload of their own.

    Used for extensions that signal their presence solely through being listed
    in the request's active extensions (e.g. the event extension itself, or
    extensions that carry payloads only via message parts but have no
    top-level metadata block and no recognized event envelope).
    """

    def collect(self, uri: str, request_context: "RequestContext") -> None:
        return None


class TaskMetadataCollector:
    """Collector that reads an extension's payload from params.metadata[uri].

    The standard delivery mechanism for extensions whose payload is a single
    typed object attached at the request level (e.g. daemon, traceability,
    distribution). Validates the raw proto value through payload_model and
    raises ExtensionActivationError when the payload is absent or malformed.
    """

    def __init__(self, payload_model: Type[A2ABaseModel]) -> None:
        self.payload_model = payload_model

    def collect(self, uri: str, request_context: "RequestContext") -> Any:
        raw_metadata = dict(request_context.metadata) if request_context.metadata else {}
        payload = raw_metadata.get(uri)
        if payload is None:
            raise ExtensionActivationError(
                uri,
                reason="the extension was declared active but its payload is "
                       "missing from the request metadata",
            )
        try:
            return self.payload_model.model_validate(proto_to_dict(payload))
        except Exception as ex:
            raise ExtensionActivationError(uri, reason=str(ex)) from ex


class MessagesCollector:
    """Collector for extensions whose payload arrives via message parts (event extension).

    collect() reads the CloudEvents envelope from message.metadata[EVENT_URI],
    validates the event type against this collector's known types, iterates
    message.parts dispatching each by its schema_uri, and returns a fully
    typed Event. Returns None (without raising) when:
    - the message is absent
    - the event envelope is missing (non-event request with messaging active)
    - the event type is not one this collector recognizes

    This means extensions.get(MESSAGING_URI) is the parsed Event for
    event-driven requests and None for direct A2A requests, with no separate
    extract_event() call needed anywhere downstream.

    Each payload class passed to the constructor must declare:
        SCHEMA_URI: ClassVar[str]  matched against part.metadata[EVENT_URI].schema
        EVENT_TYPE: ClassVar[str]  CloudEvents `type` for this payload kind

    Subclass and override schema_dispatch() / known_event_types() when the
    standard ClassVar convention does not fit.
    """

    def __init__(self, *payload_classes: Type[A2ABaseModel]) -> None:
        self._classes = payload_classes

    def collect(self, uri: str, request_context: "RequestContext") -> Optional["Event"]:
        from aion.core.runtime.context.models import Event

        message = request_context.message
        if message is None:
            return None

        if EVENT_EXTENSION_URI_V1 not in message.metadata:
            return None

        try:
            event_meta = EventMessageMetadataV1.model_validate(
                proto_to_dict(message.metadata[EVENT_EXTENSION_URI_V1])
            )
        except Exception:
            return None

        if event_meta.type not in self.known_event_types():
            return None

        schema_dispatch = self.schema_dispatch()
        payload: Optional[NormalizedPayload] = None
        payload_error: Optional[Exception] = None
        raw: Optional[SourceSystemEventPayload] = None

        for part in message.parts:
            if EVENT_EXTENSION_URI_V1 not in part.metadata:
                continue
            try:
                part_meta = EventPartMetadataV1.model_validate(
                    proto_to_dict(part.metadata[EVENT_EXTENSION_URI_V1])
                )
            except Exception:
                continue

            payload_cls = schema_dispatch.get(part_meta.schema_uri)
            if payload_cls is not None and payload is None:
                try:
                    payload = payload_cls.model_validate(proto_to_dict(part.data))
                except Exception as exc:
                    # A part tagged with a schema this collector knows, but whose
                    # data does not validate, is a malformed payload — a sender
                    # bug, not "no event". Remember the first such failure so it
                    # can be surfaced below if no other part yields a valid
                    # payload. Not raised inline: a later part carrying the same
                    # (or another known) schema may still validate, and that
                    # fallback must win — parity with the pre-existing behaviour.
                    if payload_error is None:
                        payload_error = exc

            if part_meta.schema_uri == SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1 and raw is None:
                try:
                    raw = SourceSystemEventPayload.model_validate(proto_to_dict(part.data))
                except Exception:
                    pass

        if payload is None:
            if payload_error is not None:
                # A known-schema part was present but malformed. Fail closed with
                # a diagnostic that names the schema violation (honouring the
                # ExtensionPayloadCollector contract: "Raises … payload … fails
                # validation") rather than returning None, which downstream reads
                # as "no event on the request at all" — a misleading diagnostic
                # for what is really a sender-side payload bug.
                raise ExtensionActivationError(
                    uri,
                    reason=(
                        "a message part declared a known event schema but its "
                        f"payload failed validation: {payload_error}"
                    ),
                )
            return None

        return Event(
            kind=event_meta.type,
            payload=payload,
            id=event_meta.id,
            source=event_meta.source,
            raw=raw,
        )

    def schema_dispatch(self) -> dict[str, Type[A2ABaseModel]]:
        """Map schema_uri → payload class for part-level dispatch."""
        return {cls.SCHEMA_URI: cls for cls in self._classes}

    def known_event_types(self) -> frozenset[str]:
        """CloudEvents type values this collector's classes declare as primary events."""
        return frozenset(
            cls.EVENT_TYPE
            for cls in self._classes
            if hasattr(cls, "EVENT_TYPE") and cls.EVENT_TYPE is not None
        )


@dataclass(frozen=True)
class ExtensionDescriptor:
    """Registration of a known A2A extension - the single record used for
    both per-request collection/verification and AgentCard advertisement.

    Attributes:
        uri: Extension identifier, matched against the inbound message's
            declared extensions and/or request-level metadata keys.
        collector: Strategy object that knows how to extract and validate
            this extension's payload from an inbound request. Defaults to
            MarkerCollector (no payload). Use TaskMetadataCollector for
            extensions delivered via params.metadata[uri], MessagesCollector
            for extensions delivered via message parts (returns a typed Event),
            or a custom implementation for anything else.
        requires: Other extension URIs that must also be active whenever
            this one is active. Directional - the required extension does
            not need this one active in return. Checked against extensions
            that are both declared by the client and active for the current
            agent, not merely declared.
        description: Human-readable summary, surfaced verbatim on the
            AgentCard's advertised AgentExtension entry.
        active: Whether this extension is enabled for the current agent.
            Defaults to True - most protocol-level extensions (daemon,
            traceability, distribution, ...) are active out of the box.
            Agent-specific features (evolution, reflection) register with
            active=False and rely on AgentConfig.enabled_extensions to
            turn them on.
        unavailable_reason: When set, the extension is enabled but cannot
            actually function on this deployment (e.g. its optional toolkit
            is not installed) - a request declaring it is rejected with
            exactly this user-facing message. None means available. Marked
            at startup via AionA2AExtensionRegistry.mark_unavailable() by
            whichever component owns the extension's runtime dependencies.
    """

    uri: str
    collector: ExtensionPayloadCollector = field(default_factory=MarkerCollector)
    requires: tuple[str, ...] = ()
    description: str = ""
    active: bool = True
    unavailable_reason: Optional[str] = None
