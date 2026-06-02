from typing import Optional

from aion.core.runtime.context.models import AionRuntimeContext
from google.adk.agents import InvocationContext


class AionInvocationContext(InvocationContext):
    """Extended InvocationContext that carries an Aion runtime context.

    Adds `aion_runtime_context` so that ADK agents can introspect the inbound
    Aion event, distribution, behavior, environment and identity data without
    touching server state directly.
    """

    aion_runtime_context: Optional[AionRuntimeContext] = None
