from typing import Dict, List, Literal, Optional

from pydantic import Field

from aion.shared.a2a import A2ABaseModel

__all__ = [
    "TraceStateEntry",
    "TraceabilityExtensionV1",
    "TRACEABILITY_EXTENSION_URI_V1",
]

TRACEABILITY_EXTENSION_URI_V1 = "https://docs.aion.to/extensions/aion/traceability/1.0.0"


class TraceStateEntry(A2ABaseModel):
    """Single vendor entry in the tracestate ordered list."""
    key: str
    value: str


class TraceabilityExtensionV1(A2ABaseModel):
    """
    Aion Traceability extension payload for A2A metadata.

    Implements W3C trace context propagation (traceparent/tracestate/baggage).

    Spec: https://docs.aion.to/extensions/aion/traceability/1.0.0
    """
    version: Literal["1.0.0"] = "1.0.0"
    traceparent: Optional[str] = None
    tracestate: Optional[List[TraceStateEntry]] = None
    baggage: Optional[Dict[str, str]] = Field(
        default=None,
        description="Max 8192 bytes, up to 64 entries. 'aion.*' namespace is reserved.",
    )
