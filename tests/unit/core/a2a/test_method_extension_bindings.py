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
    DeleteContextParams,
    GetContextParams,
    GetContextsParams,
)
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.core.constants.a2a import CONTEXT_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry


class TestBindings:
    def test_the_three_context_methods_are_bound(self):
        assert set(AION_JSONRPC_METHOD_EXTENSION_BINDINGS) == {"GetContexts", "GetContext", "DeleteContext"}

    @pytest.mark.parametrize(
        ("method", "uri", "params_model", "handler_name"),
        [
            ("GetContexts", CONTEXT_EXTENSION_URI_V1, GetContextsParams, "on_get_contexts"),
            ("GetContext", CONTEXT_EXTENSION_URI_V1, GetContextParams, "on_get_context"),
            ("DeleteContext", CONTEXT_EXTENSION_URI_V1, DeleteContextParams, "on_delete_context"),
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

    def test_the_context_methods_share_one_extension(self):
        """All three methods belong to the one unified Context extension."""
        uris = {binding.extension_uri for binding in AION_JSONRPC_METHOD_EXTENSION_BINDINGS.values()}

        assert uris == {CONTEXT_EXTENSION_URI_V1}

    def test_the_context_extension_is_active_and_advertised(self):
        """The standard server implements the whole contract, so it declares it."""
        descriptors = {d.uri: d for d in aion_a2a_extension_registry.get_all()}
        descriptor = descriptors[CONTEXT_EXTENSION_URI_V1]

        assert descriptor.active is True
        assert descriptor.advertised is True
