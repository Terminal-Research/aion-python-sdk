import pytest

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

    async def cancel(self, context):
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


class TestRoutedExtensionMetadataKey:
    def test_is_platform_owned(self):
        """Must stay under the platform metadata prefix so
        A2ATaskDeduplicator protects it from being overwritten by incoming
        task patches. See aion.server.tasks.deduplicator.PLATFORM_METADATA_PREFIX.
        """
        from aion.server.tasks.deduplicator import PLATFORM_METADATA_PREFIX

        assert ROUTED_EXTENSION_METADATA_KEY.startswith(PLATFORM_METADATA_PREFIX)
