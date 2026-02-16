import uuid
from contextvars import ContextVar
from typing import Dict, Optional

from pydantic import BaseModel, Field

__all__ = [
    "RequestContext",
    "TraceContext",
    "AionContext",
    "A2AContext",
    "AgentFrameworkTraceContext",
    "AgentFrameworkContext",
    "InboundContext",
    "RuntimeContext",
    "ExecutionContext",
    "execution_context_var",
]


class RequestContext(BaseModel):
    """HTTP request metadata extracted from the incoming request."""
    method: str = "POST"
    path: str = "/"
    jrpc_method: Optional[str] = None


class TraceContext(BaseModel):
    """W3C distributed tracing context propagated via traceparent header and baggage."""
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


class AionContext(BaseModel):
    """Aion platform deployment metadata identifying the running agent."""
    distribution_id: Optional[str] = None
    version_id: Optional[str] = None
    environment_id: Optional[str] = None


class A2AContext(BaseModel):
    """A2A protocol task state for the current request."""
    task_id: Optional[str] = None
    task_status: Optional[str] = None


class AgentFrameworkTraceContext(BaseModel):
    """Trace baggage scoped to the agent framework layer.

    Plugins and framework integrations use this to propagate key-value metadata
    (e.g. "langgraph.node") through the execution context without polluting the
    inbound W3C baggage.
    """
    baggage: Dict[str, str] = Field(default_factory=dict)


class AgentFrameworkContext(BaseModel):
    """Runtime context owned by the agent framework."""
    trace: AgentFrameworkTraceContext = Field(default_factory=AgentFrameworkTraceContext)


class InboundContext(BaseModel):
    """Protocol-level context set once at request entry.

    Carries everything that arrived from outside: HTTP metadata, W3C tracing
    headers, Aion deployment info, and A2A task state. Should not be mutated
    after the request is accepted.
    """
    request: RequestContext = Field(default_factory=RequestContext)
    trace: TraceContext = Field(default_factory=TraceContext)
    aion: AionContext = Field(default_factory=AionContext)
    a2a: A2AContext = Field(default_factory=A2AContext)

    @property
    def transaction_name(self) -> str:
        name = f"{self.request.method} {self.request.path}"
        if self.request.jrpc_method:
            name = f"{name} [{self.request.jrpc_method}]"
        return name


class RuntimeContext(BaseModel):
    """Mutable context accumulated during request processing.

    Updated by the agent framework and plugins as execution progresses.
    Use update_agent_framework_baggage() to write into agent_framework.trace.baggage.
    """
    agent_framework: AgentFrameworkContext = Field(default_factory=AgentFrameworkContext)


class ExecutionContext(BaseModel):
    """Per-request execution context stored in a ContextVar.

    Split into two lifecycle zones:
    - inbound: protocol-level data set once at request entry (request, trace, aion, a2a)
    - runtime: mutable state accumulated during execution (agent_framework baggage)

    Access via get_context() / set_context(). Use set_context_from_a2a() to
    initialise from A2A protocol extensions at request entry.
    """
    inbound: InboundContext = Field(default_factory=InboundContext)
    runtime: RuntimeContext = Field(default_factory=RuntimeContext)


execution_context_var: ContextVar[Optional[ExecutionContext]] = ContextVar('execution_context', default=None)
