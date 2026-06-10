"""Aion runtime context — models, builder, and provider registry.

Provides the AionRuntimeContext that carries inbound A2A event data,
distribution metadata, and identity information to agent execution code.
"""

from .builder import AionRuntimeContextBuilder
from .models import (
    AionExtensions,
    AionRuntimeContext,
    Event,
    EventKind,
    NormalizedPayload,
)
from .registry import (
    AionRuntimeContextProvider,
    AionRuntimeContextRegistry,
    get_aion_runtime_context,
    aget_aion_runtime_context,
)

__all__ = [
    "AionExtensions",
    "AionRuntimeContext",
    "AionRuntimeContextBuilder",
    "Event",
    "EventKind",
    "NormalizedPayload",
    "AionRuntimeContextProvider",
    "AionRuntimeContextRegistry",
    "get_aion_runtime_context",
    "aget_aion_runtime_context",
]
