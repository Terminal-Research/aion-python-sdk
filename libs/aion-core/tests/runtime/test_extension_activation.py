"""Tests for A2A extension collection, verification, and the registry's
per-agent activation state."""

import pytest
from a2a.types import Message, Role
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from aion.core.a2a import A2ABaseModel
from aion.core.runtime.context.extensions.descriptors import (
    ExtensionActivationError,
    ExtensionDescriptor,
    TaskMetadataCollector,
)
from aion.core.runtime.context.extensions.pipeline import (
    AionRuntimeExtensions,
    _collect,
    _verify,
)
from aion.core.runtime.context.extensions.registry import aion_a2a_extension_registry

MARKER_URI = "aion://extensions/marker/v1"
PAYLOAD_URI = "aion://extensions/payload/v1"
REQUIRES_URI = "aion://extensions/requires-marker/v1"


class _Payload(A2ABaseModel):
    value: str


def _struct(fields: dict) -> Struct:
    s = Struct()
    ParseDict(fields, s)
    return s


class _FakeRequestContext:
    """Duck-types the subset of a2a.server.agent_execution.RequestContext
    the collector reads: .message, .metadata, .requested_extensions."""

    def __init__(self, message=None, metadata=None, requested_extensions=frozenset()):
        self.message = message
        self.metadata = metadata or {}
        self.requested_extensions = requested_extensions


class TestCollect:
    def test_active_uris_union_declared_and_metadata(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        msg.extensions.append(MARKER_URI)
        rc = _FakeRequestContext(message=msg, metadata={PAYLOAD_URI: _struct({"value": "x"})})

        active_uris = _collect(rc)

        assert active_uris == {MARKER_URI, PAYLOAD_URI}

    def test_no_message_only_metadata(self):
        rc = _FakeRequestContext(message=None, metadata={PAYLOAD_URI: _struct({"value": "x"})})

        active_uris = _collect(rc)

        assert active_uris == {PAYLOAD_URI}

    def test_requested_extensions_header_signal_is_included(self):
        """A2A-Extensions is the spec-defined per-request activation signal
        (RequestContext.requested_extensions, sourced from the HTTP/gRPC
        header) and is distinct from message.extensions[]. A client that
        activates purely via the header, without also listing the URI on
        the message, must still be recognized as active - otherwise the
        request silently falls through to the plain agent instead of being
        routed or rejected."""
        msg = Message(message_id="m1", role=Role.ROLE_USER)  # extensions[] left empty on purpose
        rc = _FakeRequestContext(message=msg, metadata={}, requested_extensions=frozenset({MARKER_URI}))

        active_uris = _collect(rc)

        assert active_uris == {MARKER_URI}

    def test_nothing_active_returns_empty(self):
        rc = _FakeRequestContext(message=None, metadata={})

        active_uris = _collect(rc)

        assert active_uris == frozenset()


class TestVerify:
    """_verify() sorts each declared-active URI into one of three outcomes:
    no matching descriptor (unknown, ignored), a descriptor that is not
    active for this agent (rejected outright), or an active descriptor
    (checked against requires/payload as usual). Descriptors constructed
    directly here default to active=True, matching pre-registry-activation
    behavior unless a test says otherwise."""

    def test_inactive_descriptor_is_not_present(self):
        descriptor = ExtensionDescriptor(uri=MARKER_URI)

        verified = _verify(frozenset(), _FakeRequestContext(), [descriptor])

        assert MARKER_URI not in verified

    def test_marker_extension_is_active_with_no_payload(self):
        descriptor = ExtensionDescriptor(uri=MARKER_URI)

        verified = _verify(frozenset({MARKER_URI}), _FakeRequestContext(), [descriptor])

        assert MARKER_URI in verified
        assert verified[MARKER_URI] is None

    def test_payload_extension_parses_typed_instance(self):
        descriptor = ExtensionDescriptor(uri=PAYLOAD_URI, collector=TaskMetadataCollector(_Payload))

        verified = _verify(
            frozenset({PAYLOAD_URI}),
            _FakeRequestContext(metadata={PAYLOAD_URI: _struct({"value": "hello"})}),
            [descriptor],
        )

        assert verified[PAYLOAD_URI].value == "hello"

    def test_active_payload_extension_missing_metadata_raises(self):
        descriptor = ExtensionDescriptor(uri=PAYLOAD_URI, collector=TaskMetadataCollector(_Payload))

        with pytest.raises(ExtensionActivationError) as exc_info:
            _verify(frozenset({PAYLOAD_URI}), _FakeRequestContext(), [descriptor])

        assert exc_info.value.uri == PAYLOAD_URI

    def test_malformed_payload_raises(self):
        descriptor = ExtensionDescriptor(uri=PAYLOAD_URI, collector=TaskMetadataCollector(_Payload))

        with pytest.raises(ExtensionActivationError):
            _verify(
                frozenset({PAYLOAD_URI}),
                _FakeRequestContext(metadata={PAYLOAD_URI: _struct({"unexpected": "field-only, no value"})}),
                [descriptor],
            )

    def test_missing_required_co_activation_raises(self):
        """This is the evolution+daemon case: an extension declared active
        without an extension it requires must be rejected during
        verification, not silently routed or left for the handler to
        discover deep in business logic."""
        descriptor = ExtensionDescriptor(uri=MARKER_URI, requires=(REQUIRES_URI,))

        with pytest.raises(ExtensionActivationError) as exc_info:
            _verify(frozenset({MARKER_URI}), _FakeRequestContext(), [descriptor])

        assert exc_info.value.missing_requires == frozenset({REQUIRES_URI})

    def test_satisfied_co_activation_passes(self):
        descriptor = ExtensionDescriptor(uri=MARKER_URI, requires=(REQUIRES_URI,))
        requires_descriptor = ExtensionDescriptor(uri=REQUIRES_URI)

        verified = _verify(
            frozenset({MARKER_URI, REQUIRES_URI}),
            _FakeRequestContext(),
            [descriptor, requires_descriptor],
        )

        assert MARKER_URI in verified
        assert REQUIRES_URI in verified

    def test_unregistered_uri_is_silently_ignored(self):
        """No matching descriptor at all - the first of the three outcomes -
        is not an error, just absence."""
        verified = _verify(frozenset({MARKER_URI}), _FakeRequestContext(), [])  # no descriptors at all

        assert verified == {}

    def test_registered_but_inactive_extension_raises(self):
        """A descriptor that exists but is not active for this agent (e.g.
        AgentConfig.enabled_extensions did not list it) must reject a
        request that declares it anyway, not silently ignore it like a
        truly unknown URI - the client asked for something this specific
        agent does not support."""
        descriptor = ExtensionDescriptor(uri=MARKER_URI, active=False)

        with pytest.raises(ExtensionActivationError) as exc_info:
            _verify(frozenset({MARKER_URI}), _FakeRequestContext(), [descriptor])

        assert exc_info.value.uri == MARKER_URI

    def test_requires_checks_against_enabled_not_merely_declared(self):
        """A required extension that the client declared but that is itself
        registered-but-inactive for this agent must not satisfy `requires` -
        otherwise a disabled extension could be treated as co-activated just
        because the client happened to mention its URI."""
        descriptor = ExtensionDescriptor(uri=MARKER_URI, requires=(REQUIRES_URI,))
        inactive_requires_descriptor = ExtensionDescriptor(uri=REQUIRES_URI, active=False)

        with pytest.raises(ExtensionActivationError) as exc_info:
            _verify(
                frozenset({MARKER_URI, REQUIRES_URI}),
                _FakeRequestContext(),
                [descriptor, inactive_requires_descriptor],
            )

        assert exc_info.value.missing_requires == frozenset({REQUIRES_URI})


class TestAionRuntimeExtensionsRegisteredOnly:
    """Only registered, active extensions are tracked - unregistered URIs
    the client declared are never recognized as active, even though the raw
    collector would have seen them."""

    def test_unregistered_active_uri_reads_as_inactive(self):
        verified = _verify(frozenset({MARKER_URI}), _FakeRequestContext(), [])  # no descriptors registered at all
        active = AionRuntimeExtensions(verified)

        assert active.is_active(MARKER_URI) is False
        assert MARKER_URI not in active

    def test_iter_yields_verified_uris_only(self):
        """__iter__ is what per-agent enforcement will walk to check each
        active extension against AgentConfig.enabled_extensions."""
        verified = _verify(frozenset({MARKER_URI}), _FakeRequestContext(), [ExtensionDescriptor(uri=MARKER_URI)])
        active = AionRuntimeExtensions(verified)

        assert set(active) == {MARKER_URI}

    def test_empty_uris_is_active_returns_true(self):
        active = AionRuntimeExtensions({})

        assert active.is_active() is True


class TestAionRuntimeExtensionsCollect:
    """AionRuntimeExtensions.collect() is the entry point builders use: collect
    then verify in one step, straight from a RequestContext."""

    def test_collect_verifies_registered_active_extension(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        rc = _FakeRequestContext(message=msg, metadata={PAYLOAD_URI: _struct({"value": "x"})})
        descriptor = ExtensionDescriptor(uri=PAYLOAD_URI, collector=TaskMetadataCollector(_Payload))

        active = AionRuntimeExtensions.collect(rc, [descriptor])

        assert active.get(PAYLOAD_URI).value == "x"

    def test_missing_required_co_activation_raises(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        rc = _FakeRequestContext(message=msg, metadata={}, requested_extensions=frozenset({MARKER_URI}))
        descriptor = ExtensionDescriptor(uri=MARKER_URI, requires=(REQUIRES_URI,))

        with pytest.raises(ExtensionActivationError):
            AionRuntimeExtensions.collect(rc, [descriptor])


class TestAionA2AExtensionRegistry:
    """register()/activate()/reset_to_default()/get_all() together form the
    single source of truth both per-request enforcement and (eventually)
    AgentCard advertisement read from. activate() is additive only - it
    turns on exactly the descriptors it's given, leaving every other
    descriptor's current state (typically its registered default)
    untouched. reset_to_default() is the test-only escape hatch that
    restores every descriptor to the active value it was registered with -
    production calls activate() exactly once per agent and never needs it."""

    def setup_method(self):
        aion_a2a_extension_registry.reset_to_default()

    def teardown_method(self):
        aion_a2a_extension_registry.reset_to_default()

    def test_registered_default_is_preserved_until_activated(self):
        fake_uri = "aion://extensions/test-registry-default-active/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=False))

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)

        assert descriptor.active is False

    def test_activate_turns_on_named_uris_without_touching_others(self):
        named_uri = "aion://extensions/test-registry-named/v1"
        other_inactive_uri = "aion://extensions/test-registry-other-inactive/v1"
        other_active_uri = "aion://extensions/test-registry-other-active/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=named_uri, active=False))
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=other_inactive_uri, active=False))
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=other_active_uri, active=True))

        aion_a2a_extension_registry.activate([named_uri])

        descriptors = {d.uri: d.active for d in aion_a2a_extension_registry.get_all()}
        assert descriptors[named_uri] is True
        assert descriptors[other_inactive_uri] is False
        assert descriptors[other_active_uri] is True

    def test_activate_with_unknown_uri_is_a_noop(self):
        """A URI in AgentConfig.enabled_extensions with no matching
        descriptor must not raise - it just has nothing to turn on."""
        aion_a2a_extension_registry.activate(["aion://extensions/does-not-exist/v1"])

    def test_reset_to_default_restores_registered_value(self):
        fake_uri = "aion://extensions/test-registry-reset/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=False))
        aion_a2a_extension_registry.activate([fake_uri])
        assert next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri).active is True

        aion_a2a_extension_registry.reset_to_default()

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)
        assert descriptor.active is False

    def test_description_round_trips(self):
        fake_uri = "aion://extensions/test-registry-metadata/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=fake_uri, description="A test extension.")
        )

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)

        assert descriptor.description == "A test extension."

    def test_reflection_extension_defaults_inactive_and_requires_daemon(self):
        """Reflection (behaviour evolution) is an agent-specific business
        feature with no A2A spec entry - like evolution, it must require an
        agent's AgentConfig.enabled_extensions to opt in, and is
        daemon-driven only (the improver is daemon-addressable)."""
        from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1, BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1

        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        assert descriptors[BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1].active is False
        assert descriptors[BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1].requires == (DAEMON_EXTENSION_URI_V1,)

    def test_core_protocol_extensions_default_active(self):
        """Per the A2A extension specs, traceability/distribution/messaging/
        event/cards all activate without any agent-level opt-in - only
        opt-in features (daemon-scoped invocation, evolution) default
        inactive and require AgentConfig.enabled_extensions. Regression
        guard for registry.py's registration defaults."""
        from aion.core.constants.a2a import (
            CARDS_EXTENSION_URI_V1,
            DAEMON_EXTENSION_URI_V1,
            DISTRIBUTION_EXTENSION_URI_V1,
            EVENT_EXTENSION_URI_V1,
            MESSAGING_EXTENSION_URI_V1,
            TRACEABILITY_EXTENSION_URI_V1,
        )

        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        for uri in (
            TRACEABILITY_EXTENSION_URI_V1,
            DISTRIBUTION_EXTENSION_URI_V1,
            MESSAGING_EXTENSION_URI_V1,
            EVENT_EXTENSION_URI_V1,
            CARDS_EXTENSION_URI_V1,
        ):
            assert descriptors[uri].active is True
        assert descriptors[DAEMON_EXTENSION_URI_V1].active is False


class TestMarkUnavailable:
    def setup_method(self):
        aion_a2a_extension_registry.reset_to_default()

    def teardown_method(self):
        aion_a2a_extension_registry.reset_to_default()

    def test_mark_unavailable_records_reason(self):
        fake_uri = "aion://extensions/test-registry-unavailable/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=True))

        aion_a2a_extension_registry.mark_unavailable(fake_uri, "toolkit not installed")

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)
        assert descriptor.unavailable_reason == "toolkit not installed"

    def test_mark_unavailable_unknown_uri_is_a_noop(self):
        aion_a2a_extension_registry.mark_unavailable(
            "aion://extensions/does-not-exist/v1", "whatever"
        )

    def test_reset_restores_availability(self):
        fake_uri = "aion://extensions/test-registry-unavailable-reset/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=True))
        aion_a2a_extension_registry.mark_unavailable(fake_uri, "gone")

        aion_a2a_extension_registry.reset_to_default()

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)
        assert descriptor.unavailable_reason is None
