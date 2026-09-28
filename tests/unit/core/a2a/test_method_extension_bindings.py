"""The JSON-RPC bindings for Aion's A2A method extensions.

A method extension adds an RPC method and is still an ordinary extension, so
its identity lives in the shared registry and only its transport routing lives
here. These tests hold the two sides together: every binding must name an
extension the registry actually knows, or the method would have an exposure
policy governing nothing.
"""

import pytest

from aion.core.a2a import (
    AION_JSONRPC_METHOD_EXTENSION_BINDINGS,
    AionJsonRpcMethodExtensionBinding,
    GetContextParams,
    GetContextsListParams,
)
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.core.constants.a2a import (
    GET_CONTEXT_EXTENSION_URI_V1,
    GET_CONTEXTS_LIST_EXTENSION_URI_V1,
)
from aion.core.runtime import aion_a2a_extension_registry


class TestBindings:
    def test_both_context_methods_are_bound(self):
        assert set(AION_JSONRPC_METHOD_EXTENSION_BINDINGS) == {"GetContext", "GetContexts"}

    @pytest.mark.parametrize(
        ("method", "uri", "params_model", "handler_name"),
        [
            ("GetContext", GET_CONTEXT_EXTENSION_URI_V1, GetContextParams, "on_get_context"),
            (
                "GetContexts",
                GET_CONTEXTS_LIST_EXTENSION_URI_V1,
                GetContextsListParams,
                "on_get_contexts_list",
            ),
        ],
    )
    def test_each_binding_names_its_extension_model_and_handler(
        self, method, uri, params_model, handler_name
    ):
        binding = AION_JSONRPC_METHOD_EXTENSION_BINDINGS[method]

        assert binding.extension_uri == uri
        assert binding.params_model is params_model
        assert binding.handler_name == handler_name

    def test_every_binding_names_a_handler_the_request_handler_has(self):
        """The dispatcher resolves the handler by this name, so a typo here is
        an AttributeError on a real call rather than at import."""
        for method, binding in AION_JSONRPC_METHOD_EXTENSION_BINDINGS.items():
            assert callable(getattr(AionRequestHandler, binding.handler_name, None)), method

    def test_a_method_name_is_bound_once(self):
        """A mapping cannot hold a duplicate key, so this is a guard on the
        shape of the table rather than on its content: a list of bindings, or
        a second table merged into this one, could.
        """
        methods = list(AION_JSONRPC_METHOD_EXTENSION_BINDINGS)

        assert len(methods) == len(set(methods))

    def test_the_table_cannot_be_mutated_at_runtime(self):
        """Which methods this server answers is decided at import, not by
        whatever ran last."""
        with pytest.raises(TypeError):
            AION_JSONRPC_METHOD_EXTENSION_BINDINGS["Whatever"] = None  # type: ignore[index]


class TestBindingsAgainstTheRegistry:
    def test_every_binding_names_a_registered_extension(self):
        """The consistency check the extension_uri field exists for.

        A binding pointing at a URI no descriptor claims would be a method
        whose activation, availability and Agent Card exposure are governed
        by nothing - the failure this must catch.
        """
        registered = {d.uri for d in aion_a2a_extension_registry.get_all()}

        for method, binding in AION_JSONRPC_METHOD_EXTENSION_BINDINGS.items():
            assert binding.extension_uri in registered, method

    def test_an_unregistered_uri_is_caught(self):
        """The check above is only worth having if it can fail."""
        registered = {d.uri for d in aion_a2a_extension_registry.get_all()}
        stray = AionJsonRpcMethodExtensionBinding(
            extension_uri="aion://extensions/never-registered/v1",
            params_model=GetContextParams,
            handler_name="on_get_context",
        )

        assert stray.extension_uri not in registered

    def test_a_binding_carries_no_exposure_of_its_own(self):
        """Exposure is the descriptor's to state, and a binding that repeated
        it could contradict it. It has no field to contradict it with."""
        fields = set(AionJsonRpcMethodExtensionBinding.__dataclass_fields__)

        assert fields == {"extension_uri", "params_model", "handler_name"}
        assert not fields & {"advertised", "internal", "public", "active", "functional"}

    def test_the_bound_extensions_are_active_and_not_advertised(self):
        """Being a method extension is not what keeps them off the card - the
        descriptor's advertised flag is. See the Agent Card tests for the
        other half: a method extension may be advertised.
        """
        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}

        for binding in AION_JSONRPC_METHOD_EXTENSION_BINDINGS.values():
            descriptor = descriptors[binding.extension_uri]
            assert descriptor.active is True
            assert descriptor.advertised is False
