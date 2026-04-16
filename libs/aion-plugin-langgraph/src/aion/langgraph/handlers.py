from __future__ import annotations

from typing import Callable, Optional

from aion.shared.logging import get_logger
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime

from .context import AionContext

logger = get_logger()


def add_event_handlers(
        builder: StateGraph,
        *,
        on_message: Optional[Callable] = None,
        on_reaction: Optional[Callable] = None,
        on_command: Optional[Callable] = None,
        on_card_action: Optional[Callable] = None,
        on_event: Optional[Callable] = None,
) -> None:
    """Register event handler nodes and wire dispatcher routing into a StateGraph.

    Each handler is an ordinary LangGraph node. The dispatcher reads
    runtime.context.event.kind and routes to the matching handler.
    on_event acts as a fallback when no specific handler is registered.

    Usage:
        builder = StateGraph(State, context_schema=AionContext)
        add_event_handlers(
            builder,
            on_message=handle_message,
            on_reaction=handle_reaction,
            on_command=handle_command,
            on_card_action=handle_card_action,
            on_event=handle_event,
        )
    """
    _kind_to_handler = {
        "message": on_message,
        "reaction": on_reaction,
        "command": on_command,
        "card_action": on_card_action,
    }

    registered = [
        fn for fn in [on_message, on_reaction, on_command, on_card_action, on_event]
        if fn is not None
    ]

    def _route(_state, runtime: Runtime[AionContext]) -> str:
        if not runtime.context:
            logger.warning("No context found in runtime")
            return END

        kind = runtime.context.event.kind
        handler = _kind_to_handler.get(kind) or on_event
        if handler is None:
            logger.warning("No handler registered for event kind: %s", kind)
            return END
        return handler.__name__

    for fn in registered:
        builder.add_node(fn.__name__, fn)

    builder.add_conditional_edges(
        START,
        _route,
        {fn.__name__: fn.__name__ for fn in registered} | {END: END},
    )
