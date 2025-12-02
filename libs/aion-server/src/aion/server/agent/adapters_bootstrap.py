"""Bootstrap module to register all available adapters."""
from aion.shared.agent import adapter_registry

try:
    from aion.langgraph import LangGraphAdapter
except:
    LangGraphAdapter = None


def register_available_adapters():
    """Register all available framework adapters with database support."""
    from aion.server.db import db_manager

    supported_framework_adapters = []
    if LangGraphAdapter is not None:
        # Initialize LangGraph adapter with database manager for PostgreSQL checkpointer support
        supported_framework_adapters.append(LangGraphAdapter(db_manager=db_manager))

    for adapter in supported_framework_adapters:
        if not adapter_registry.is_registered(adapter.framework_name()):
            adapter_registry.register(adapter)
