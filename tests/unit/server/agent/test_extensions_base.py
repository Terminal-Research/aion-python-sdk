import pytest

from aion.core.runtime import aion_a2a_extension_registry
from aion.server.agent.execution.extensions.base import (
    ROUTED_EXTENSION_METADATA_KEY,
    discover_extension_task_handlers,
)
from aion.server.agent.execution.extensions import base as extensions_base_module
from aion.server.agent.execution.extensions.evolution import EvolutionTaskHandler


class _FakeHandler:
    uri = "https://docs.aion.to/a2a/extensions/aion/fake/1.0.0"

    async def availability(self, config):
        return True

    def stream(self, context):
        raise NotImplementedError

    def resume(self, context):
        raise NotImplementedError

    async def cancel(self, context, event_queue):
        raise NotImplementedError


class TestDiscoverExtensionTaskHandlers:
    def test_includes_evolution_handler_by_default(self):
        handlers = discover_extension_task_handlers()
        assert any(isinstance(handler, EvolutionTaskHandler) for handler in handlers)

    def test_instantiates_known_handler_classes(self, monkeypatch):
        monkeypatch.setattr(extensions_base_module, "_KNOWN_TASK_HANDLERS", [_FakeHandler])

        handlers = discover_extension_task_handlers()

        assert len(handlers) == 1
        assert isinstance(handlers[0], _FakeHandler)


class TestHandlersAgainstTheRegistry:
    """`ExtensionTaskHandler` is the single source of truth about which
    extension owns a task's execution.

    That is what makes an execution-owning extension structurally different
    from a metadata or payload extension - it takes stream(), resume() and
    cancel() from the framework adapter - so nothing in the descriptor needs
    a flag saying so. These tests keep the two sides consistent: every
    task-routing extension is a registered extension, and no URI is owned
    twice.
    """

    def test_every_handler_uri_has_a_registered_descriptor(self):
        """A handler whose URI nothing registers could never be routed to:
        routing matches against the request's *active* extensions, and only a
        registered descriptor can be active."""
        registered = {d.uri for d in aion_a2a_extension_registry.get_all()}

        for handler in discover_extension_task_handlers():
            assert handler.uri in registered, handler.uri

    def test_no_uri_is_owned_by_two_handlers(self):
        """The executor keys handlers by URI, so a duplicate would silently
        win or lose depending on discovery order."""
        uris = [handler.uri for handler in discover_extension_task_handlers()]

        assert len(uris) == len(set(uris))

    def test_most_registered_extensions_own_no_execution(self):
        """Metadata and payload extensions do not intercept a task.

        The complement of the check above, and the reason a `functional` or
        `has_behavior` flag on the descriptor would be both imprecise and
        redundant: whether an extension owns execution is already answered,
        exactly, by whether a handler claims its URI.
        """
        handler_uris = {handler.uri for handler in discover_extension_task_handlers()}
        registered = {d.uri for d in aion_a2a_extension_registry.get_all()}

        assert registered - handler_uris, "every extension owns execution; check discovery"


class TestRoutedExtensionMetadataKey:
    def test_is_platform_owned(self):
        """Must stay under the platform metadata prefix so
        A2ATaskDeduplicator protects it from being overwritten by incoming
        task patches. See aion.core.a2a.metadata.PLATFORM_METADATA_PREFIX.
        """
        from aion.core.a2a.metadata import PLATFORM_METADATA_PREFIX

        assert ROUTED_EXTENSION_METADATA_KEY.startswith(PLATFORM_METADATA_PREFIX)
