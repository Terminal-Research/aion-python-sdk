"""ContextVar-based ADK event emitter and invocation context.

Provides per-invocation callables that authoring code (Thread, Message, emit_*)
uses without direct access to the ADK stream executor. Both are set up by
aion-server-adk's ADKStreamExecutor before agent.run_async() begins and
reset on exit.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from google.adk.events import Event
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .invocation_context import AionInvocationContext

EventEmitter = Callable[[Event], None]

_ADK_EMITTER: ContextVar[Optional[EventEmitter]] = ContextVar("_adk_emitter", default=None)
_ADK_CTX: ContextVar[Optional[AionInvocationContext]] = ContextVar("_adk_ctx", default=None)


def get_adk_emitter() -> Optional[EventEmitter]:
    """Return the active ADK event emitter, or None if outside an invocation."""
    return _ADK_EMITTER.get()


def set_adk_emitter(emitter: EventEmitter) -> Token[Optional[EventEmitter]]:
    """Set the ADK event emitter for the current async context.

    Returns a token that must be passed to reset_adk_emitter() on exit.
    """
    return _ADK_EMITTER.set(emitter)


def reset_adk_emitter(token: Token[Optional[EventEmitter]]) -> None:
    """Reset the ADK event emitter to its previous value."""
    _ADK_EMITTER.reset(token)


def get_adk_ctx() -> Optional[AionInvocationContext]:
    """Return the active ADK InvocationContext, or None if outside an invocation."""
    return _ADK_CTX.get()


def set_adk_ctx(ctx: AionInvocationContext) -> Token[Optional[AionInvocationContext]]:
    """Set the ADK InvocationContext for the current async context.

    Returns a token that must be passed to reset_adk_ctx() on exit.
    """
    return _ADK_CTX.set(ctx)


def reset_adk_ctx(token: Token[Optional[AionInvocationContext]]) -> None:
    """Reset the ADK InvocationContext to its previous value."""
    _ADK_CTX.reset(token)
