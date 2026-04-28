from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aion.shared.tasks.protocols import AionTaskManagerProtocol

__all__ = [
    "RequestData",
    "TraceData",
    "AionData",
    "A2AData",
    "AgentFrameworkTraceData",
    "AgentFrameworkData",
    "ProtocolScope",
    "FrameworkScope",
    "ServerRuntime",
    "AgentExecutionScope",
]


class RequestData(BaseModel):
    """HTTP request metadata extracted from the incoming request."""
    method: str = "POST"
    path: str = "/"
    jrpc_method: Optional[str] = None


class TraceData(BaseModel):
    """W3C distributed tracing data propagated via traceparent header and baggage."""
    transaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    traceparent: Optional[str] = None
    baggage: Dict[str, str] = Field(default_factory=dict)

    @property
    def version(self) -> Optional[str]:
        return self._get_value_from_traceparent(0)

    @property
    def trace_id(self) -> Optional[str]:
        return self._get_value_from_traceparent(1)

    @property
    def span_id(self) -> Optional[str]:
        return self._get_value_from_traceparent(2)

    @property
    def trace_flags(self) -> Optional[str]:
        return self._get_value_from_traceparent(3)

    def _get_value_from_traceparent(self, index: int) -> Optional[str]:
        if self.traceparent is None:
            return None

        try:
            return self.traceparent.split('-')[index]
        except:
            return None


class AionData(BaseModel):
    """Aion platform deployment metadata identifying the running agent."""
    distribution_id: Optional[str] = None
    version_id: Optional[str] = None
    environment_id: Optional[str] = None


class A2AData(BaseModel):
    """A2A protocol task state for the current agent execution."""
    task_id: Optional[str] = None
    task_status: Optional[str] = None


class AgentFrameworkTraceData(BaseModel):
    """Trace baggage scoped to the agent framework layer.

    Plugins and framework integrations use this to propagate key-value metadata
    (e.g. "langgraph.node") through the execution scope without polluting the
    inbound W3C baggage.
    """
    baggage: Dict[str, str] = Field(default_factory=dict)


class AgentFrameworkData(BaseModel):
    """Runtime data owned by the agent framework."""
    trace: AgentFrameworkTraceData = Field(default_factory=AgentFrameworkTraceData)


class ProtocolScope(BaseModel):
    """Protocol-level data set once at agent execution entry.

    Carries everything that arrived from outside: HTTP metadata, W3C tracing
    headers, Aion deployment info, and A2A task state. Should not be mutated
    after the execution begins.
    """
    request: RequestData = Field(default_factory=RequestData)
    trace: TraceData = Field(default_factory=TraceData)
    aion: AionData = Field(default_factory=AionData)
    a2a: A2AData = Field(default_factory=A2AData)

    @property
    def transaction_name(self) -> str:
        name = f"{self.request.method} {self.request.path}"
        if self.request.jrpc_method:
            name = f"{name} [{self.request.jrpc_method}]"
        return name


class FrameworkScope(BaseModel):
    """Mutable framework data accumulated during agent execution.

    Updated by the agent framework and plugins as execution progresses.
    Contains mutable agent framework state and tracing information.
    """
    agent_framework: AgentFrameworkData = Field(default_factory=AgentFrameworkData)


@dataclass
class ServerRuntime:
    """Server-side runtime objects created during agent execution.

    Holds references to server components (task managers, stores, etc.) that are
    instantiated per agent execution. These objects are not protocol-level data
    and may not be serializable, hence stored separately from inbound/runtime.
    """
    task_manager: Optional[AionTaskManagerProtocol] = None


@dataclass
class AgentExecutionScope:
    """Per-agent-execution scope stored in a ContextVar.

    Container for all execution data split into three zones:
    - inbound: immutable data from execution entry (request, trace, aion, a2a)
    - framework: mutable framework state accumulated during execution (baggage, framework data)
    - server: server-side runtime objects (task managers, stores, etc.)

    Access via AgentExecutionScopeHelper.
    """
    inbound: ProtocolScope = field(default_factory=ProtocolScope)
    framework: FrameworkScope = field(default_factory=FrameworkScope)
    server: ServerRuntime = field(default_factory=ServerRuntime)
