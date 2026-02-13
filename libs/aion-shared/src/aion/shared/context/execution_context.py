import uuid
from contextvars import ContextVar
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RequestContext",
    "TraceContext",
    "AionContext",
    "A2AContext",
    "ExecutionContext",
    "request_context_var",
]


class RequestContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: str = "POST"
    path: str = "/"
    jrpc_method: Optional[str] = None


class TraceContext(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    model_config = ConfigDict(frozen=True)

    distribution_id: Optional[str] = None
    version_id: Optional[str] = None
    environment_id: Optional[str] = None


class A2AContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: Optional[str] = None
    task_status: Optional[str] = None


class ExecutionContext(BaseModel):
    """
    Represents the context of a request with all relevant tracking information
    for logging and distributed tracing.
    """
    model_config = ConfigDict(frozen=True)

    request: RequestContext = Field(default_factory=RequestContext)
    trace: TraceContext = Field(default_factory=TraceContext)
    aion: AionContext = Field(default_factory=AionContext)
    a2a: A2AContext = Field(default_factory=A2AContext)

    current_node: Optional[str] = None

    @property
    def transaction_name(self) -> str:
        name = f"{self.request.method} {self.request.path}"
        if self.request.jrpc_method:
            name = f"{name} [{self.request.jrpc_method}]"
        return name

    def update(self, **kwargs) -> 'ExecutionContext':
        """Create new instance with updated fields."""
        return self.model_copy(update=kwargs)


request_context_var: ContextVar[Optional[ExecutionContext]] = ContextVar('request_context', default=None)
