"""ContextVar-based ADK event emitter.

Provides a per-invocation callback that authoring code (Thread, Message)
uses to emit google.adk.events.Event objects without direct access to
the ADK stream executor. The emitter is set up by aion-server-adk's
ADKStreamExecutor before agent.run_async() begins and is reset on exit.
"""

from contextvars import ContextVar, Token
from typing import Callable, Optional

_ADK_EMITTER: ContextVar[Optional[Callable]] = ContextVar("_adk_emitter", default=None)


def get_adk_emitter() -> Optional[Callable]:
    """Return the active ADK event emitter, or None if outside an invocation."""
    return _ADK_EMITTER.get()


def set_adk_emitter(emitter: Callable) -> Token[Optional[Callable]]:
    """Set the ADK event emitter for the current async context.

    Returns a token that must be passed to reset_adk_emitter() on exit.
    """
    return _ADK_EMITTER.set(emitter)


def reset_adk_emitter(token: Token[Optional[Callable]]) -> None:
    """Reset the ADK event emitter to its previous value."""
    _ADK_EMITTER.reset(token)
