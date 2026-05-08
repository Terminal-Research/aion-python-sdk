from __future__ import annotations

import inspect
from aion.shared.logging import get_logger
from aion.shared.runtime.context import AionRuntimeContext
from aion.shared.runtime.context.models import EventKind
from langgraph.constants import START
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from typing import Any, Callable, Dict, List, Optional, Set

from aion.langgraph.runtime.context.thread import Thread

logger = get_logger()

# Shorthand used when constructing inspect.Parameter objects for __signature__ overrides.
_POSITIONAL = inspect.Parameter.POSITIONAL_OR_KEYWORD

# Parameter names that we resolve ourselves from AionRuntimeContext.
# Any handler parameter whose name is in this set will be populated by
# _build_context_dependencies(), not forwarded to LangGraph for injection.
_CONTEXT_PARAM_NAMES = frozenset({"context", "event", "identity", "inbox", "thread", "message"})

AION_ROUTER_NODE_NAME = "__aion_event_router__"


def _build_context_dependencies(
        state: Any,
        runtime: Optional[Runtime[AionRuntimeContext]],
) -> Dict[str, Any]:
    """Build the full set of dependencies we can resolve from AionRuntimeContext.

    Returns a dict keyed by the parameter names that handlers may declare.
    Always includes state, runtime, and context. Adds event, identity, inbox,
    thread, and message when a context is present.
    """
    context = runtime.context if runtime else None
    dependencies: Dict[str, Any] = {"state": state, "runtime": runtime, "context": context}

    if context is not None:
        dependencies["event"] = context.event
        dependencies["identity"] = context.identity
        dependencies["inbox"] = context.inbox
        thread = Thread.from_context(context)
        dependencies["thread"] = thread
        dependencies["message"] = thread.message

    return dependencies


class _HandlerInvoker:
    """Prepares and calls a single event handler with dependency injection.

    Inspects the handler signature once at construction time and caches which
    parameters it declares. At invocation time, filters the available dependencies
    down to only what the handler actually declared, then calls it.

    Supports both sync and async handlers transparently.
    """

    def __init__(self, handler: Callable) -> None:
        self._handler = handler
        self._is_async = inspect.iscoroutinefunction(handler)
        # Cached at construction — inspect is never called again after this.
        self._declared_params: Set[str] = set(inspect.signature(handler).parameters)
        # True if the handler declared any context-derived or runtime params,
        # meaning we need to call _build_context_dependencies() on invocation.
        self._needs_runtime = bool(self._declared_params & (_CONTEXT_PARAM_NAMES | {"runtime"}))

    async def invoke(self, state: Any, **langgraph_kwargs: Any) -> Any:
        """Resolve dependencies and call the handler.

        langgraph_kwargs contains whatever LangGraph injected into the dispatcher
        node (e.g. runtime, config, store). We merge them with our own resolved
        dependencies and filter down to what the handler declared.
        """
        runtime = langgraph_kwargs.get("runtime") if self._needs_runtime else None
        if self._needs_runtime:
            dependencies = _build_context_dependencies(state, runtime)
        else:
            dependencies = {"state": state}
        dependencies.update(langgraph_kwargs)

        handler_kwargs = {
            name: value
            for name, value in dependencies.items()
            if name in self._declared_params
        }
        if self._is_async:
            return await self._handler(**handler_kwargs)
        return self._handler(**handler_kwargs)


class AionEventHandlers:
    """Event dispatcher node for LangGraph graphs driven by Aion runtime context.

    Receives an inbound A2A invocation, reads the event kind from runtime context,
    and routes to the matching handler. Acts as a single LangGraph node — routing
    is an internal implementation detail, not visible in the graph structure.

    Handlers may declare any subset of the following parameters; only the declared
    ones are injected:

        state       — LangGraph graph state
        runtime     — full LangGraph Runtime[AionRuntimeContext]
        context     — AionRuntimeContext extracted from runtime
        event       — typed inbound event (kind + payload), or None for direct A2A
        identity    — agent identity derived from the distribution extension
        inbox       — raw A2A inbox (escape hatch for direct access)
        thread      — Thread bound to the current invocation
        message     — normalized inbound Message, or None if no message in inbox

    Any other declared parameter (e.g. config, store) is forwarded to LangGraph
    for native injection, making the dispatcher forward-compatible with new
    LangGraph-injectable params without code changes.

    Usage:
        builder = StateGraph(State, context_schema=AionRuntimeContext)
        add_event_handlers(
            builder,
            on_message=handle_message,   # async def handle_message(thread, message): ...
            on_reaction=handle_reaction, # async def handle_reaction(event): ...
            on_event=handle_event,       # async def handle_event(state, context): ...
        )
    """

    def __init__(
            self,
            *,
            on_message: Optional[Callable] = None,
            on_reaction: Optional[Callable] = None,
            on_command: Optional[Callable] = None,
            on_card_action: Optional[Callable] = None,
            on_event: Optional[Callable] = None,
    ) -> None:
        self._kind_map: Dict[EventKind, Callable] = {}
        if on_message:
            self._kind_map[EventKind.MESSAGE] = on_message
        if on_reaction:
            self._kind_map[EventKind.REACTION] = on_reaction
        if on_command:
            self._kind_map[EventKind.COMMAND] = on_command
        if on_card_action:
            self._kind_map[EventKind.CARD_ACTION] = on_card_action

        self._fallback = on_event
        # Used as fallback when no event is present but an inbox message exists
        # (e.g. direct A2A task/message without a CloudEvents envelope).
        self._direct_message = on_message

        all_handlers: List[Callable] = [
            handler for handler in [on_message, on_reaction, on_command, on_card_action, on_event]
            if handler is not None
        ]
        self._invokers: Dict[str, _HandlerInvoker] = {
            handler.__name__: _HandlerInvoker(handler) for handler in all_handlers
        }

        # __signature__ tells LangGraph which parameters to inject when calling
        # this node. We set it to the union of what all registered handlers need,
        # so LangGraph provides everything any handler might require.
        self.__signature__ = self._build_signature(all_handlers)

    @staticmethod
    def _build_signature(handlers: List[Callable]) -> inspect.Signature:
        """Compute the __signature__ to expose to LangGraph for this dispatcher node.

        Always includes state. Adds runtime if any handler needs context-derived
        dependencies. Adds any other params declared by handlers that are not in
        _CONTEXT_PARAM_NAMES — those are forwarded to LangGraph for native injection.
        """
        needs_runtime = any(
            bool(set(inspect.signature(handler).parameters) & (_CONTEXT_PARAM_NAMES | {"runtime"}))
            for handler in handlers
        )

        params = [inspect.Parameter("state", _POSITIONAL)]
        if needs_runtime:
            params.append(inspect.Parameter("runtime", _POSITIONAL, annotation=Runtime[AionRuntimeContext]))

        # Collect LangGraph-native params (e.g. config, store) declared by any handler.
        # We deduplicate across handlers so each param appears only once in the signature.
        already_added: Set[str] = set(_CONTEXT_PARAM_NAMES) | {"state", "runtime"}
        for handler in handlers:
            for name, handler_param in inspect.signature(handler).parameters.items():
                if name not in already_added:
                    params.append(inspect.Parameter(name, _POSITIONAL, annotation=handler_param.annotation))
                    already_added.add(name)

        return inspect.Signature(params)

    def _select_handler(self, context: Optional[AionRuntimeContext]) -> Optional[Callable]:
        """Select which handler to invoke based on the inbound event kind.

        Falls back to on_message for direct inbox messages without a CloudEvents
        envelope, and to on_event when no more specific match is found.
        """
        if context is None:
            return self._fallback

        event = context.event
        if event is None:
            if self._direct_message is not None and context.inbox.message is not None:
                return self._direct_message
            return self._fallback

        return self._kind_map.get(event.kind) or self._fallback

    async def __call__(self, state: Any, **langgraph_kwargs: Any) -> Any:
        """LangGraph node entry point. Routes the invocation to the matching handler."""
        runtime = langgraph_kwargs.get("runtime")
        context = runtime.context if runtime else None

        handler = self._select_handler(context)
        if handler is None:
            logger.warning("No handler matched for this invocation")
            return None

        return await self._invokers[handler.__name__].invoke(state, **langgraph_kwargs)

    def attach(self, builder: StateGraph) -> None:
        """Register this dispatcher as a node in the graph and connect it from START."""
        builder.add_node(AION_ROUTER_NODE_NAME, self)
        builder.add_edge(START, AION_ROUTER_NODE_NAME)


def add_event_handlers(
        builder: StateGraph,
        *,
        on_message: Optional[Callable] = None,
        on_reaction: Optional[Callable] = None,
        on_command: Optional[Callable] = None,
        on_card_action: Optional[Callable] = None,
        on_event: Optional[Callable] = None,
) -> None:
    """Register an event dispatcher node into a StateGraph.

    Creates an AionEventHandlers dispatcher and attaches it to the builder.
    See AionEventHandlers for the full list of injectable parameters and behavior.
    """
    AionEventHandlers(
        on_message=on_message,
        on_reaction=on_reaction,
        on_command=on_command,
        on_card_action=on_card_action,
        on_event=on_event,
    ).attach(builder)
