"""ADK invocation context extension for Aion runtime data.

Extends Google ADK's InvocationContext to carry Aion-specific runtime
information (environment, distribution, identity) without requiring direct
server state access.
"""

from aion.core.runtime.context.models import AionRuntimeContext
from google.adk.agents import InvocationContext
from typing import Optional


class AionInvocationContext(InvocationContext):
    """Extended InvocationContext that carries an Aion runtime context.

    Adds `aion_runtime_context` so that ADK agents can introspect the inbound
    Aion event, distribution, behavior, environment and identity data without
    touching server state directly.
    """

    aion_runtime_context: Optional[AionRuntimeContext] = None
    """Optional Aion runtime context providing access to event payload, environment,
    distribution network, and identity information for the current invocation."""
