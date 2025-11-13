"""Bootstrap module to register all available adapters."""
from aion.shared.agent import adapter_registry


def register_available_adapters():
    """Register all available framework adapters."""
    from .langgraph import LangGraphAdapter

    supported_framework_adapters = (
        LangGraphAdapter,
    )

    for adapter in supported_framework_adapters:
        if not adapter_registry.is_registered(adapter.framework_name()):
            adapter_registry.register(adapter())
