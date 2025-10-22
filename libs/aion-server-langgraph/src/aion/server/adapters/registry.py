from typing import Any, Optional

from aion.shared.metaclasses import Singleton

from .base.agent_adapter import AgentAdapter


class AdapterRegistry(metaclass=Singleton):
    """Singleton registry for managing framework adapters."""

    _adapters: dict[str, AgentAdapter] = {}

    def register(self, adapter: AgentAdapter) -> None:
        """Register a new framework adapter."""
        framework_name = adapter.framework_name()
        if framework_name in self._adapters:
            raise ValueError(
                f"Adapter for framework '{framework_name}' is already registered"
            )
        self._adapters[framework_name] = adapter

    def unregister(self, framework_name: str) -> None:
        """Unregister an adapter by framework name."""
        self._adapters.pop(framework_name, None)

    def get_adapter(self, framework_name: str) -> Optional[AgentAdapter]:
        """Get an adapter by framework name."""
        return self._adapters.get(framework_name)

    def get_adapter_for_agent(self, agent_obj: Any) -> Optional[AgentAdapter]:
        """Find and return the appropriate adapter for a given agent object."""
        for adapter in self._adapters.values():
            if adapter.can_handle(agent_obj):
                return adapter
        return None

    def list_adapters(self) -> list[AgentAdapter]:
        """Return a list of all registered adapter instances."""
        return list(self._adapters.values())

    def list_registered_frameworks(self) -> list[str]:
        """Return a list of all registered framework names."""
        return list(self._adapters.keys())

    def clear(self) -> None:
        """Clear all registered adapters."""
        self._adapters.clear()

    def is_registered(self, framework_name: str) -> bool:
        """Check if an adapter is registered for the given framework name."""
        return framework_name in self._adapters


adapter_registry = AdapterRegistry()
