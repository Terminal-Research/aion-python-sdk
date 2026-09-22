"""The Google ADK scenario agent.

One ``BaseAgent`` whose run parses the inbound text and hands it to a
behaviour. Every answer goes out through the Aion ``Thread``, never as a
yielded ADK event, so a scenario that fails points at the SDK rather than
at this agent's wiring.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from aion.adk.authoring.invocation import AionInvocationContext, Thread
from google.adk.agents import BaseAgent
from google.adk.events import Event
from typing_extensions import override

from tests.scenarios.agents.adk_core.behaviors import BEHAVIORS
from tests.scenarios.agents.adk_core.invocation import Invocation
from tests.scenarios.agents.extensions import register_scenario_extensions
from tests.scenarios.commands import help_text, not_implemented_text, parse_command

__all__ = ["ScenarioAgent", "create_agent"]

AGENT_NAME = "scenario_agent"

register_scenario_extensions()


class ScenarioAgent(BaseAgent):
    """Answers the scenario command contract on Google ADK."""

    @override
    async def _run_async_impl(self, ctx: AionInvocationContext) -> AsyncGenerator[Event, None]:
        """Answer the parsed command.

        An unknown command answers with the menu; a command the registry
        declares but this agent does not implement says so in as many words.
        """
        runtime = ctx.aion_runtime_context
        if runtime is None:
            raise RuntimeError("the ADK invocation carries no Aion runtime context")

        thread = Thread.from_context(runtime)
        text = (thread.message.text if thread.message is not None else "") or ""
        command = parse_command(text)

        if not command.known:
            await thread.reply(help_text())
        elif (behavior := BEHAVIORS.get(command.key)) is None:
            await thread.reply(not_implemented_text(command.key))
        else:
            # A behaviour that answers through the thread returns nothing; one
            # that hands the server an A2A payload returns the ADK event
            # carrying it, which only the agent itself can yield.
            emitted = await behavior(Invocation(thread=thread, context=runtime, command=command))
            if emitted is not None:
                yield emitted


def create_agent() -> ScenarioAgent:
    """Build the agent."""
    return ScenarioAgent(name=AGENT_NAME, description="Answers the scenario command contract")
