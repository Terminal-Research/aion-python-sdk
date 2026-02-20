from typing import Optional

from aion.shared.types.a2a.models import A2AInbox
from google.adk.agents import InvocationContext


class AionInvocationContext(InvocationContext):
    """Extended InvocationContext that carries an A2A inbox snapshot.

    Adds `a2a_inbox` so that ADK agents can introspect the inbound A2A
    Task / Message / metadata without touching server state directly.
    """

    a2a_inbox: Optional[A2AInbox] = None
