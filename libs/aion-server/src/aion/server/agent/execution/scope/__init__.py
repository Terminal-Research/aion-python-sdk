from .types import (
    RequestData,
    TraceData,
    AionData,
    A2AData,
    AgentFrameworkTraceData,
    AgentFrameworkData,
    ProtocolScope,
    FrameworkScope,
    ExecutionRuntime,
    AgentExecutionScope,
)
from .helper import (
    init_execution_scope,
    get_execution_scope,
    clear_execution_scope,
    set_distribution,
    set_traceability,
    set_request,
    set_task_id,
    set_task_status,
    set_agent_framework_baggage,
    set_task_manager,
    get_task_manager,
    set_aion_runtime_context,
    get_aion_runtime_context,
)

__all__ = [
    # Types
    "RequestData",
    "TraceData",
    "AionData",
    "A2AData",
    "AgentFrameworkTraceData",
    "AgentFrameworkData",
    "ProtocolScope",
    "FrameworkScope",
    "ExecutionRuntime",
    "AgentExecutionScope",
    # Scope management functions
    "init_execution_scope",
    "get_execution_scope",
    "clear_execution_scope",
    # A2A extension setters
    "set_distribution",
    "set_traceability",
    "set_request",
    # Data mutation functions
    "set_task_id",
    "set_task_status",
    "set_agent_framework_baggage",
    "set_task_manager",
    "get_task_manager",
    "set_aion_runtime_context",
    "get_aion_runtime_context",
]
