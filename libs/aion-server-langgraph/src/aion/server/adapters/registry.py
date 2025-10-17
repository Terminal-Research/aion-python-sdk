from typing import Any, Optional

from aion.server.adapters.base.agent_adapter import AgentAdapter
from aion.shared.metaclasses import Singleton

class AdapterRegistry(metaclass=Singleton):
    _instance: Optional["AdapterRegistry"] = None
    _adapters: dict[str, AgentAdapter] = {}

    def register(self, adapter: AgentAdapter) -> None:

        framework_name = adapter.framework_name
        if framework_name in self._adapters:
            raise ValueError(
                f"Adapter for framework '{framework_name}' is already registered"
            )
        self._adapters[framework_name] = adapter

    def unregister(self, framework_name: str) -> None:

        self._adapters.pop(framework_name, None)

    def get_adapter(self, framework_name: str) -> Optional[AgentAdapter]:

        return self._adapters.get(framework_name)

    def get_adapter_for_agent(self, agent_obj: Any) -> Optional[AgentAdapter]:

        for adapter in self._adapters.values():
            if adapter.can_handle(agent_obj):
                return adapter
        return None

    def list_adapters(self) -> list[str]:
        return list(self._adapters.keys())

    def clear(self) -> None:
        self._adapters.clear()

    def is_registered(self, framework_name: str) -> bool:

        return framework_name in self._adapters
_global_registry = AdapterRegistry()

def register_adapter(adapter: AgentAdapter) -> None:

    _global_registry.register(adapter)

def get_adapter(framework_name: str) -> Optional[AgentAdapter]:

    return _global_registry.get_adapter(framework_name)

def get_adapter_for_agent(agent_obj: Any) -> Optional[AgentAdapter]:

    return _global_registry.get_adapter_for_agent(agent_obj)

def list_registered_frameworks() -> list[str]:
    return _global_registry.list_adapters()

def clear_registry() -> None:
    _global_registry.clear()


