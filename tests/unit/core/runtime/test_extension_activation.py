"""Tests for A2A extension collection, verification, and the registry's
per-agent activation state."""

from types import SimpleNamespace

import pytest
from a2a.types import Message, Role
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from aion.core.a2a import A2ABaseModel
from aion.core.runtime.context.extensions.descriptors import (
    ExtensionActivationError,
    ExtensionDescriptor,
    HeaderCollector,
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

    def __init__(
        self,
        message=None,
        metadata=None,
        requested_extensions=frozenset(),
        headers=None,
    ):
        self.message = message
        self.metadata = metadata or {}
        self.requested_extensions = requested_extensions
        self.call_context = SimpleNamespace(state={"headers": headers or {}})


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

    def test_header_extension_preserves_opaque_value(self):
        descriptor = ExtensionDescriptor(
            uri=PAYLOAD_URI,
            collector=HeaderCollector("Aion-Usage-Attribution"),
        )

        verified = _verify(
            frozenset({PAYLOAD_URI}),
            _FakeRequestContext(
                headers={"aion-usage-attribution": "  signed-token  "}
            ),
            [descriptor],
        )

        assert verified[PAYLOAD_URI] == "signed-token"

    def test_header_extension_rejects_missing_value(self):
        descriptor = ExtensionDescriptor(
            uri=PAYLOAD_URI,
            collector=HeaderCollector("Aion-Usage-Attribution"),
        )

        with pytest.raises(ExtensionActivationError, match="is missing or empty"):
            _verify(
                frozenset({PAYLOAD_URI}),
                _FakeRequestContext(),
                [descriptor],
            )

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
    single source of truth both per-request enforcement and AgentCard
    advertisement read from - the card through get_advertised(), everything
    request-time through get_all(). activate() is additive only - it
    turns on exactly the descriptors it's given, leaving every other
    descriptor's current state (typically its registered default)
    untouched. reset_to_default() is the test-only escape hatch that
    restores every descriptor to the active value it was registered with -
    production calls activate() exactly once per agent and never needs it."""

    @pytest.fixture(autouse=True)
    def _registry(self, isolated_registry):
        pass

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
        before = {d.uri: d.active for d in aion_a2a_extension_registry.get_all()}
        aion_a2a_extension_registry.activate(["aion://extensions/does-not-exist/v1"])
        after = {d.uri: d.active for d in aion_a2a_extension_registry.get_all()}

        assert after == before

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
    @pytest.fixture(autouse=True)
    def _registry(self, isolated_registry):
        pass

    def test_mark_unavailable_records_reason(self):
        fake_uri = "aion://extensions/test-registry-unavailable/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=True))

        aion_a2a_extension_registry.mark_unavailable(fake_uri, "toolkit not installed")

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)
        assert descriptor.unavailable_reason == "toolkit not installed"

    def test_mark_unavailable_unknown_uri_is_a_noop(self):
        before = {d.uri for d in aion_a2a_extension_registry.get_all()}
        aion_a2a_extension_registry.mark_unavailable(
            "aion://extensions/does-not-exist/v1", "whatever"
        )
        after = {d.uri for d in aion_a2a_extension_registry.get_all()}

        assert after == before

    def test_reset_restores_availability(self):
        fake_uri = "aion://extensions/test-registry-unavailable-reset/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=fake_uri, active=True))
        aion_a2a_extension_registry.mark_unavailable(fake_uri, "gone")

        aion_a2a_extension_registry.reset_to_default()

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == fake_uri)
        assert descriptor.unavailable_reason is None


class TestAdvertisement:
    """`advertised` decides publication on the AgentCard and nothing else.

    Three properties, kept apart: registered at all (the server knows the
    URI), active (enabled for this agent), advertised (may be published).
    get_advertised() is the only place that combines them, so the card
    builder never reinterprets the registry.
    """

    @pytest.fixture(autouse=True)
    def _registry(self, isolated_registry):
        pass

    def test_defaults_to_advertised(self):
        """Most extensions are ordinary capabilities; withholding one is the
        deliberate case, so it is the one that has to be spelled out."""
        assert ExtensionDescriptor(uri=MARKER_URI).advertised is True

    def test_get_advertised_omits_inactive_descriptors(self):
        uri = "aion://extensions/test-advertised-inactive/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=uri, active=False, advertised=True)
        )

        assert uri not in {d.uri for d in aion_a2a_extension_registry.get_advertised()}

    def test_get_advertised_omits_non_advertised_descriptors(self):
        uri = "aion://extensions/test-advertised-withheld/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=uri, active=True, advertised=False)
        )

        assert uri not in {d.uri for d in aion_a2a_extension_registry.get_advertised()}

    def test_get_advertised_omits_unavailable_descriptors(self):
        """Enabled and publishable, but this deployment cannot serve it - and
        the request-time verifier already refuses it with the same reason."""
        uri = "aion://extensions/test-advertised-unavailable/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=uri, active=True))
        aion_a2a_extension_registry.mark_unavailable(uri, "toolkit not installed")

        assert uri not in {d.uri for d in aion_a2a_extension_registry.get_advertised()}

    def test_get_all_still_returns_a_non_advertised_descriptor(self):
        """The whole point of the flag: request-time behaviour is unchanged."""
        uri = "aion://extensions/test-advertised-still-known/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=uri, active=True, advertised=False)
        )

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == uri)
        assert descriptor.active is True

    def test_a_non_advertised_extension_is_verified_like_any_other(self):
        """Not advertised is not an authorization decision.

        A request declaring the URI is collected and verified exactly as it
        would be for an advertised extension - the flag never reaches
        _verify().
        """
        uri = "aion://extensions/test-advertised-verified/v1"
        descriptor = ExtensionDescriptor(uri=uri, advertised=False)
        msg = Message(message_id="m1", role=Role.ROLE_USER)
        rc = _FakeRequestContext(
            message=msg, metadata={}, requested_extensions=frozenset({uri})
        )

        extensions = AionRuntimeExtensions.collect(rc, [descriptor])

        assert extensions.is_active(uri) is True
        assert extensions.unknown == ()

    def test_advertising_does_not_change_activation(self):
        """Withholding a descriptor from the card leaves activate() alone."""
        uri = "aion://extensions/test-advertised-activation/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=uri, active=False, advertised=False)
        )

        aion_a2a_extension_registry.activate([uri])

        descriptor = next(d for d in aion_a2a_extension_registry.get_all() if d.uri == uri)
        assert descriptor.active is True
        assert descriptor.advertised is False

    def test_context_read_extensions_are_active_and_not_advertised(self):
        """The built-in case the flag exists for.

        Both methods stay registered, enabled and callable; neither is
        announced on a standard agent's card. Regression guard for
        registry.py's registrations - see
        docs/development/extension-exposure.md.
        """
        from aion.core.constants.a2a import (
            GET_CONTEXT_EXTENSION_URI_V1,
            GET_CONTEXTS_LIST_EXTENSION_URI_V1,
        )

        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        for uri in (GET_CONTEXT_EXTENSION_URI_V1, GET_CONTEXTS_LIST_EXTENSION_URI_V1):
            assert descriptors[uri].active is True
            assert descriptors[uri].advertised is False

    def test_a_custom_registration_can_advertise_a_context_uri(self):
        """An implementation that genuinely fulfills a broader contract may
        say so: register() replaces by URI, and that stayed true."""
        from aion.core.constants.a2a import GET_CONTEXT_EXTENSION_URI_V1

        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=GET_CONTEXT_EXTENSION_URI_V1, advertised=True)
        )

        assert GET_CONTEXT_EXTENSION_URI_V1 in {
            d.uri for d in aion_a2a_extension_registry.get_advertised()
        }

    def test_get_advertised_omits_a_descriptor_whose_requirement_is_inactive(self):
        """`requires` decides advertisement too, not only verification.

        A client reading the card would declare this extension and be refused
        for the co-activation it could not have known was missing.
        """
        required = "aion://extensions/test-advertised-required/v1"
        dependent = "aion://extensions/test-advertised-dependent/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=required, active=False)
        )
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=dependent, requires=(required,))
        )

        assert dependent not in {d.uri for d in aion_a2a_extension_registry.get_advertised()}

    def test_get_advertised_keeps_a_descriptor_whose_requirement_is_active(self):
        required = "aion://extensions/test-advertised-required-on/v1"
        dependent = "aion://extensions/test-advertised-dependent-on/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=required))
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=dependent, requires=(required,))
        )

        assert dependent in {d.uri for d in aion_a2a_extension_registry.get_advertised()}

    def test_a_requirement_that_is_active_but_unavailable_withholds_its_dependent(self):
        """The chain is only as strong as its weakest link, so the check has
        to keep narrowing rather than stop one level down."""
        required = "aion://extensions/test-advertised-chain-required/v1"
        middle = "aion://extensions/test-advertised-chain-middle/v1"
        dependent = "aion://extensions/test-advertised-chain-dependent/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=required))
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=middle, requires=(required,))
        )
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=dependent, requires=(middle,))
        )
        aion_a2a_extension_registry.mark_unavailable(required, "toolkit not installed")

        advertised = {d.uri for d in aion_a2a_extension_registry.get_advertised()}
        assert middle not in advertised
        assert dependent not in advertised

    def test_a_requirement_need_not_itself_be_advertised(self):
        """Advertising is about what a card says; verification only needs the
        requirement active, so withholding it from the card changes nothing."""
        required = "aion://extensions/test-advertised-hidden-required/v1"
        dependent = "aion://extensions/test-advertised-hidden-dependent/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=required, advertised=False)
        )
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=dependent, requires=(required,))
        )

        advertised = {d.uri for d in aion_a2a_extension_registry.get_advertised()}
        assert dependent in advertised
        assert required not in advertised


class TestServiceability:
    """`unservable_reason()` is the deployment-readiness half of an extension URI.

    Registered is a fact about the code; ready is a fact about this
    deployment right now, and the two move on different clocks. Readiness is
    the whole question for a method extension invoked directly, which sends
    no extension declaration of its own. An extension declared on a message
    is held to this and then to per-request activation in _verify(), where
    its requirements must be declared on that same request - so a URI ready
    here can still be refused there.
    """

    @pytest.fixture(autouse=True)
    def _registry(self, isolated_registry):
        pass

    def test_a_registered_active_available_extension_is_servable(self):
        uri = "aion://extensions/test-servable-ok/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=uri))

        assert aion_a2a_extension_registry.unservable_reason(uri) is None

    def test_an_unregistered_uri_is_not_servable(self):
        reason = aion_a2a_extension_registry.unservable_reason("aion://extensions/nope/v1")

        assert reason is not None
        assert "not registered" in reason

    def test_an_inactive_extension_names_enabled_extensions(self):
        """Named the way _verify() names it, because it is the same fix."""
        uri = "aion://extensions/test-servable-inactive/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=uri, active=False))

        reason = aion_a2a_extension_registry.unservable_reason(uri)

        assert reason is not None
        assert "enabled_extensions" in reason

    def test_an_unavailable_extension_reports_its_own_reason(self):
        uri = "aion://extensions/test-servable-unavailable/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=uri))
        aion_a2a_extension_registry.mark_unavailable(uri, "toolkit not installed")

        assert aion_a2a_extension_registry.unservable_reason(uri) == "toolkit not installed"

    def test_a_missing_requirement_is_named(self):
        required = "aion://extensions/test-servable-required/v1"
        dependent = "aion://extensions/test-servable-dependent/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=required, active=False)
        )
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=dependent, requires=(required,))
        )

        reason = aion_a2a_extension_registry.unservable_reason(dependent)

        assert reason is not None
        assert required in reason

    def test_an_unavailable_requirement_is_named(self):
        """Available is part of servable, so a requirement that cannot run
        withholds its dependent as surely as an inactive one."""
        required = "aion://extensions/test-servable-required-broken/v1"
        dependent = "aion://extensions/test-servable-dependent-broken/v1"
        aion_a2a_extension_registry.register(ExtensionDescriptor(uri=required))
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=dependent, requires=(required,))
        )
        aion_a2a_extension_registry.mark_unavailable(required, "gone")

        reason = aion_a2a_extension_registry.unservable_reason(dependent)

        assert reason is not None
        assert required in reason

    def test_advertisement_does_not_decide_serviceability(self):
        """Both directions, because either one slipping would turn the card
        into an access-control mechanism."""
        withheld = "aion://extensions/test-servable-withheld/v1"
        announced = "aion://extensions/test-servable-announced/v1"
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=withheld, advertised=False)
        )
        aion_a2a_extension_registry.register(
            ExtensionDescriptor(uri=announced, active=False, advertised=True)
        )

        assert aion_a2a_extension_registry.unservable_reason(withheld) is None
        assert aion_a2a_extension_registry.unservable_reason(announced) is not None

    def test_the_context_read_extensions_are_servable_as_shipped(self):
        """The methods this matters for: enforcement that refused them would
        be a behaviour change, not a check."""
        from aion.core.constants.a2a import (
            GET_CONTEXT_EXTENSION_URI_V1,
            GET_CONTEXTS_LIST_EXTENSION_URI_V1,
        )

        for uri in (GET_CONTEXT_EXTENSION_URI_V1, GET_CONTEXTS_LIST_EXTENSION_URI_V1):
            assert aion_a2a_extension_registry.unservable_reason(uri) is None
