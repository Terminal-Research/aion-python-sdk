import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any

__all__ = [
    "RequestContext",
    "request_context_var",
]


@dataclass
class RequestContext:
    """
    Represents the context of a request with all relevant tracking information
    for Logstash logging and distributed tracing.
    """
    # Transaction-level identifiers
    transaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    transaction_name: Optional[str] = None

    # Trace context (from A2A metadata)
    trace_id: Optional[str] = None  # from aion:traceId
    span_id: Optional[str] = None
    span_name: Optional[str] = None
    parent_span_id: Optional[str] = None

    # User context
    user_id: Optional[str] = None  # from aion:senderId

    # Aion-specific required tags (from A2A metadata)
    aion_distribution_id: Optional[str] = None  # from aion:distribution.id
    aion_version_id: Optional[str] = None  # from aion:distribution.behavior.versionId
    aion_agent_environment_id: Optional[str] = None  # from aion:distribution.environment.id

    # Additional context for logging
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)

    def update(self, **kwargs) -> 'RequestContext':
        """Create new instance with updated fields"""
        current_data = self.to_dict()
        # Handle tags separately to merge them properly
        if 'tags' in kwargs and self.tags:
            merged_tags = {**self.tags, **kwargs['tags']}
            kwargs['tags'] = merged_tags
        current_data.update(kwargs)
        return RequestContext(**current_data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RequestContext':
        """Create RequestContext from dictionary"""
        # Filter only known fields to avoid TypeError
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)

    def create_span_context(self, span_name: str, span_id: str = None) -> 'RequestContext':
        """
        Create a new context for a span operation.

        Args:
            span_name: Human-readable span name (e.g., "langgraph.execute")
            span_id: Optional span ID, generates UUID if not provided
        """
        return self.update(
            span_id=span_id or str(uuid.uuid4()),
            span_name=span_name,
            parent_span_id=self.span_id  # Current span becomes parent
        )

# Single context variable to hold the entire context
request_context_var: ContextVar[Optional[RequestContext]] = ContextVar('request_context', default=None)
