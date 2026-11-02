"""The Google ADK scenario agent.

One ``BaseAgent`` whose run parses the inbound text and hands it to a
behaviour. Every answer goes out through the Aion ``Thread``, never as a
yielded ADK event, so a scenario that fails points at the SDK rather than
at this agent's wiring.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional

from aion.adk.authoring.invocation import AionInvocationContext, Thread
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types
from typing_extensions import override

from tests.scenarios.agents.adk_core.behaviors import BEHAVIORS
from tests.scenarios.agents.adk_core.behaviors.interrupts import REQUEST_INPUT, resume
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

        answered = _answered_call(ctx)
        if answered is not None:
            # The turn answers a question this agent asked: ADK hands the
            # reply over as the function response to the asking call.
            call, answer = answered
            emitted = await resume(Invocation(thread=thread, context=runtime, command=command), call, answer)
            if emitted is not None:
                yield emitted
        elif not command.known:
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


def _answered_call(ctx: AionInvocationContext) -> Optional[tuple[types.FunctionCall, str]]:
    """The question this turn answers and the answer, when its user content is a reply to one."""
    parts = (ctx.user_content.parts if ctx.user_content is not None else None) or []
    for part in parts:
        response = part.function_response
        if response is None or response.name != REQUEST_INPUT:
            continue
        for event in reversed(ctx.session.events):
            for call in event.get_function_calls():
                if call.id == response.id:
                    return call, str((response.response or {}).get("result", ""))
    return None


def create_agent() -> ScenarioAgent:
    """Build the agent."""
    return ScenarioAgent(name=AGENT_NAME, description="Answers the scenario command contract")
