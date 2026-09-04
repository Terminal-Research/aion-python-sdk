"""ADK agent invocation runtime — context, threading, and event emission.

Provides the core abstractions for managing ADK agent execution:
- Thread: conversation thread bound to the current invocation
- Message: inbound message with reaction support
- AionInvocationContext: runtime context carrying Aion data
- emit_*: helpers for streaming responses (messages, cards, artifacts, reactions)
- Context variables: per-invocation emitter and context storage
"""

from .context_vars import (
    get_adk_ctx,
    get_adk_emitter,
    reset_adk_ctx,
    reset_adk_emitter,
    set_adk_ctx,
    set_adk_emitter,
)
from .emitters import (
    emit_artifact,
    emit_card,
    emit_reaction,
    emit_message,
)
from .invocation_context import AionInvocationContext
from .message import Message, User
from .thread import Thread

__all__ = [
    "AionInvocationContext",
    "Message",
    "Thread",
    "User",
    "get_adk_ctx",
    "get_adk_emitter",
    "reset_adk_ctx",
    "reset_adk_emitter",
    "set_adk_ctx",
    "set_adk_emitter",
    "emit_artifact",
    "emit_card",
    "emit_reaction",
    "emit_message",
]
