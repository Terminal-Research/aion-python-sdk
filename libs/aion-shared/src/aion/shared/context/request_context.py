import uuid
from contextvars import ContextVar
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any

__all__ = [
    "RequestContext",
    "request_context_var",
]

from aion.shared.opentelemetry import SpanInfo


@dataclass
class RequestContext:
    """
    Represents the context of a request with all relevant tracking information
    for Logstash logging and distributed tracing.
    """
    # Transaction-level identifiers
    transaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_method: str = field(default="POST")
    request_path: str = field(default="/")
    request_jrpc_method: Optional[str] = field(default=None)

    # Trace context (from A2A metadata)
    trace_id: Optional[str] = None  # from aion:traceId

    # User context
    user_id: Optional[str] = None  # from aion:senderId

    # Aion-specific required tags (from A2A metadata)
    aion_distribution_id: Optional[str] = None  # from aion:distribution.id
    aion_version_id: Optional[str] = None  # from aion:distribution.behavior.versionId
    aion_agent_environment_id: Optional[str] = None  # from aion:distribution.environment.id
    langgraph_current_node: Optional[str] = None

    @property
    def transaction_name(self) -> str:
        name = f"{self.request_method} {self.request_path}"
        if self.request_jrpc_method:
            name = name + f" [{self.request_jrpc_method}]"
        return name

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)

    def update(self, **kwargs) -> 'RequestContext':
        """Create new instance with updated fields"""
        current_data = self.to_dict()
        current_data.update(kwargs)
        return RequestContext(**current_data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RequestContext':
        """Create RequestContext from dictionary"""
        # Filter only known fields to avoid TypeError
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)

    def set_langgraph_current_node(self, langgraph_current_node: str) -> None:
        self.langgraph_current_node = langgraph_current_node


# Single context variable to hold the entire context
request_context_var: ContextVar[Optional[RequestContext]] = ContextVar('request_context', default=None)
