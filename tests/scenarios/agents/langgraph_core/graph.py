"""The LangGraph scenario agent.

Two nodes: the Aion event router, which records what arrived, and one
behaviour node, which answers it. Every command shares that path, so a
scenario that fails points at the SDK rather than at this agent's wiring.
"""

from __future__ import annotations

from aion.core.runtime import AionRuntimeContext
from aion.langgraph.authoring import create_event_router
from aion.langgraph.authoring.invocation import Thread
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime

from tests.scenarios.agents.langgraph_core.behaviors import BEHAVIORS
from tests.scenarios.agents.langgraph_core.invocation import Invocation
from tests.scenarios.agents.langgraph_core.state import ScenarioState
from tests.scenarios.commands import help_text, not_implemented_text, parse_command

__all__ = ["create_graph"]

ROUTER_NODE = "aion_events"
BEHAVIOR_NODE = "behavior"


def _inbound(thread: Thread, handler: str) -> dict:
    """State written by whichever router handler received the turn."""
    return {"text": (thread.message.text if thread.message else "") or "", "handler": handler}


async def on_invoke(thread: Thread) -> dict:
    """A direct A2A request, with no Aion event envelope around it."""
    return _inbound(thread, "on_invoke")


async def on_message(thread: Thread) -> dict:
    """A message event from a distribution."""
    return _inbound(thread, "on_message")


async def on_reaction(thread: Thread) -> dict:
    """A reaction event from a distribution."""
    return _inbound(thread, "on_reaction")


async def on_command(thread: Thread) -> dict:
    """A command event from a distribution."""
    return _inbound(thread, "on_command")


async def on_card_action(thread: Thread) -> dict:
    """A card action event from a distribution."""
    return _inbound(thread, "on_card_action")


async def on_event(thread: Thread) -> dict:
    """An event whose kind has no handler of its own."""
    return _inbound(thread, "on_event")


async def run_behavior(state: ScenarioState, *, runtime: Runtime[AionRuntimeContext]) -> dict | None:
    """Answer the parsed command.

    An unknown command answers with the menu; a command the registry declares
    but this agent does not implement says so in as many words.
    """
    command = parse_command(state.get("text", ""))
    thread = Thread.from_context(runtime.context)

    if not command.known:
        await thread.reply(help_text())
        return None

    behavior = BEHAVIORS.get(command.key)
    if behavior is None:
        await thread.reply(not_implemented_text(command.key))
        return None

    return await behavior(
        Invocation(
            thread=thread,
            context=runtime.context,
            command=command,
            handler=state.get("handler", "on_invoke"),
        )
    )


def create_graph() -> StateGraph:
    """Build the agent's graph."""
    workflow = StateGraph(ScenarioState, context_schema=AionRuntimeContext)

    workflow.add_node(
        ROUTER_NODE,
        create_event_router(
            on_message=on_message,
            on_reaction=on_reaction,
            on_command=on_command,
            on_card_action=on_card_action,
            on_event=on_event,
            on_invoke=on_invoke,
        ),
    )
    workflow.add_node(BEHAVIOR_NODE, run_behavior)

    workflow.add_edge(START, ROUTER_NODE)
    workflow.add_edge(ROUTER_NODE, BEHAVIOR_NODE)
    workflow.add_edge(BEHAVIOR_NODE, END)

    return workflow
