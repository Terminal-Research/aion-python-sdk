"""A2A extension models for distributed traceability.

Defines payload models for OpenTelemetry-based tracestate and trace metadata
propagation across A2A message boundaries.
"""

from typing import Dict, List, Literal, Optional

from pydantic import Field

from aion.core.a2a import A2ABaseModel

__all__ = [
    "TraceStateEntry",
    "TraceabilityExtensionV1",
]


class TraceStateEntry(A2ABaseModel):
    """Single vendor entry in the tracestate ordered list."""

    key: str = Field(description="Vendor-specific tracestate key, e.g. 'aion'.")
    value: str = Field(description="Opaque vendor-specific tracestate value.")


class TraceabilityExtensionV1(A2ABaseModel):
    """Aion Traceability extension payload for A2A metadata.

    Implements W3C trace context propagation (traceparent/tracestate/baggage).

    Spec: https://docs.aion.to/extensions/aion/traceability/1.0.0
    """

    version: Literal["1.0.0"] = Field(
        default="1.0.0",
        description="Traceability extension schema version; always '1.0.0'.",
    )
    traceparent: Optional[str] = Field(
        default=None,
        description="W3C traceparent header value encoding trace-id, parent-id, and trace-flags.",
    )
    tracestate: Optional[List[TraceStateEntry]] = Field(
        default=None,
        description="Ordered list of vendor-specific trace state entries.",
    )
    baggage: Optional[Dict[str, str]] = Field(
        default=None,
        description="Max 8192 bytes, up to 64 entries. 'aion.*' namespace is reserved.",
    )
