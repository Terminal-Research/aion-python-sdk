"""Bootstrap module to register all available adapters."""


def register_available_adapters():
    """Register all available framework adapters."""
    from aion.server.adapters.registry import adapter_registry
    from aion.server.adapters.langgraph import LangGraphAdapter

    supported_framework_adapters = (
        LangGraphAdapter,
    )

    for adapter in supported_framework_adapters:
        if not adapter_registry.is_registered(adapter.framework_name()):
            adapter_registry.register(adapter())
