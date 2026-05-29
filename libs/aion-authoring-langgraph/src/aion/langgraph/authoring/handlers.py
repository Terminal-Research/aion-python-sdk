from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional, Set

from aion.core.logging import get_logger
from aion.core.runtime.context import AionRuntimeContext
from aion.core.runtime.context.models import EventKind
from aion.langgraph.authoring.invocation.thread import Thread
from langgraph.runtime import Runtime

logger = get_logger()

# Shorthand used when constructing inspect.Parameter objects for __signature__ overrides.
_POSITIONAL = inspect.Parameter.POSITIONAL_OR_KEYWORD

# Parameter names that we resolve ourselves from AionRuntimeContext.
# Any handler parameter whose name is in this set will be populated by
# _build_context_dependencies(), not forwarded to LangGraph for injection.
_CONTEXT_PARAM_NAMES = frozenset({
    "context",
    "event",
    "distribution",
    "behavior",
    "environment",
    "principal_identity",
    "service_identity",
    "inbox",
    "thread",
    "message",
})


def _build_context_dependencies(
        state: Any,
        runtime: Optional[Runtime[AionRuntimeContext]],
) -> Dict[str, Any]:
    """Build the full set of dependencies we can resolve from AionRuntimeContext.

    Returns a dict keyed by the parameter names that handlers may declare.
    Always includes state, runtime, and context. Adds the Aion event,
    distribution-derived models, principal/service identities, inbox, thread,
    and message when a context is present.
    """
    context = runtime.context if runtime else None
    dependencies: Dict[str, Any] = {"state": state, "runtime": runtime, "context": context}

    if context is not None:
        dependencies["event"] = context.event
        dependencies["distribution"] = context.get_distribution()
        dependencies["behavior"] = context.get_behavior()
        dependencies["environment"] = context.get_environment()
        dependencies["principal_identity"] = context.get_principal_identity()
        dependencies["service_identity"] = context.get_service_identity()
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


class AionEventRouter:
    """Event router node for LangGraph graphs driven by Aion runtime context.

    Receives an inbound A2A invocation, reads the event kind from runtime
    context, and calls the matching handler. It is a normal LangGraph node:
    graph authors add it with ``builder.add_node(...)`` and own every incoming
    and outgoing edge.

    Handlers may declare any subset of the following parameters; only the declared
    ones are injected:

        state              — LangGraph graph state.
        runtime            — Full LangGraph `Runtime[AionRuntimeContext]`.
        context            — `AionRuntimeContext` extracted from runtime.
        event              — Typed inbound event, or None when no event exists.
        distribution       — Distribution model from the Aion Distribution extension.
        behavior           — Behavior model from the Aion Distribution extension.
        environment        — Environment model from the Aion Distribution extension.
        principal_identity — Principal identity, or None when absent.
        service_identity   — Service identity, or None when absent.
        inbox              — Raw A2A inbox escape hatch.
        thread             — Thread bound to the current invocation.
        message            — Normalized inbound Message, or None if no message exists.

    Any other declared parameter (e.g. config, store) is forwarded to LangGraph
    for native injection, making the router forward-compatible with new
    LangGraph-injectable params without code changes.

    Routing priority:
        1. ``on_message``, ``on_reaction``, ``on_command``, or
           ``on_card_action`` matched by event kind.
        2. ``on_event`` when an event is present but its kind has no registered
           specific handler.
        3. ``on_invoke`` when there is no Aion event.

    Usage:
        builder = StateGraph(State, context_schema=AionRuntimeContext)
        builder.add_node(
            "aion_events",
            create_event_router(
                on_message=handle_message,   # async def handle_message(thread, message): ...
                on_reaction=handle_reaction, # async def handle_reaction(event): ...
                on_event=handle_event,       # async def handle_event(state, context): ...
                on_invoke=handle_invoke,     # async def handle_invoke(state, context): ...
            ),
        )
        builder.add_edge(START, "aion_events")
        builder.add_edge("aion_events", END)

    Args:
        on_message: Called when the inbound event kind is MESSAGE.
        on_reaction: Called when the inbound event kind is REACTION.
        on_command: Called when the inbound event kind is COMMAND.
        on_card_action: Called when the inbound event kind is CARD_ACTION.
        on_event: Fallback when an event is present but its kind has no
            specific handler.
        on_invoke: Called when there is no Aion event.
    """

    def __init__(
            self,
            *,
            on_message: Optional[Callable] = None,
            on_reaction: Optional[Callable] = None,
            on_command: Optional[Callable] = None,
            on_card_action: Optional[Callable] = None,
            on_event: Optional[Callable] = None,
            on_invoke: Optional[Callable] = None,
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

        self._on_event = on_event
        self._on_invoke = on_invoke

        all_handlers: List[Callable] = [
            handler for handler in [on_message, on_reaction, on_command, on_card_action, on_event, on_invoke]
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
        """Compute the signature exposed to LangGraph for this router node.

        Always includes state. Adds runtime if any handler needs context-derived
        dependencies. Adds any other params declared by handlers that are not in
        ``_CONTEXT_PARAM_NAMES`` so LangGraph can inject them natively.
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
        """Select which handler to invoke based on the inbound event kind."""
        if not isinstance(context, AionRuntimeContext):
            return self._on_invoke

        event = context.event
        if event is None:
            return self._on_invoke

        return self._kind_map.get(event.kind) or self._on_event

    async def __call__(self, state: Any, **langgraph_kwargs: Any) -> Any:
        """Invoke the matching event handler as a normal LangGraph node."""
        runtime = langgraph_kwargs.get("runtime")
        context = runtime.context if runtime else None

        handler = self._select_handler(context)
        if handler is None:
            logger.warning("No handler matched for this invocation")
            return None

        return await self._invokers[handler.__name__].invoke(state, **langgraph_kwargs)


def create_event_router(
        *,
        on_message: Optional[Callable] = None,
        on_reaction: Optional[Callable] = None,
        on_command: Optional[Callable] = None,
        on_card_action: Optional[Callable] = None,
        on_event: Optional[Callable] = None,
        on_invoke: Optional[Callable] = None,
) -> AionEventRouter:
    """Create an Aion event router for explicit LangGraph node registration.

    The returned object is a callable LangGraph node. Add it to a graph with
    ``builder.add_node(...)`` and connect it with ordinary LangGraph edges.

    Args:
        on_message: Called when the inbound event kind is MESSAGE.
        on_reaction: Called when the inbound event kind is REACTION.
        on_command: Called when the inbound event kind is COMMAND.
        on_card_action: Called when the inbound event kind is CARD_ACTION.
        on_event: Fallback when an event is present but its kind has no
            specific handler.
        on_invoke: Called when there is no Aion event.

    Returns:
        A callable event router node.
    """
    return AionEventRouter(
        on_message=on_message,
        on_reaction=on_reaction,
        on_command=on_command,
        on_card_action=on_card_action,
        on_event=on_event,
        on_invoke=on_invoke,
    )
