"""Bootstrap module to register all available adapters."""
from aion.shared.agent import adapter_registry


def register_available_adapters():
    """Register all available framework adapters.

    This function registers both LangGraph and ADK adapters.
    """
    from .adk import ADKAdapter
    from .langgraph import LangGraphAdapter

    supported_framework_adapters = (
        LangGraphAdapter,
        ADKAdapter,
    )

    for adapter_class in supported_framework_adapters:
        framework_name = adapter_class.framework_name()
        if not adapter_registry.is_registered(framework_name):
            adapter_registry.register(adapter_class())
