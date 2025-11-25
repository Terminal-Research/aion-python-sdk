"""Bootstrap module to register all available adapters."""
from aion.shared.agent import adapter_registry

try:
    from aion.langgraph import LangGraphAdapter
except:
    LangGraphAdapter = None


def register_available_adapters():
    """Register all available framework adapters."""
    supported_framework_adapters = []
    if LangGraphAdapter is not None:
        supported_framework_adapters.append(LangGraphAdapter)

    for adapter in supported_framework_adapters:
        if not adapter_registry.is_registered(adapter.framework_name()):
            adapter_registry.register(adapter())
