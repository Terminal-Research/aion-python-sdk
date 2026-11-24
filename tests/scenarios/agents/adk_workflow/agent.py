"""An ADK Workflow that routes each message by its first word.

    text <rest>    a function node returns a string: the reply
    data <rest>    a function node returns a dict: the structured response
    ask            a node asks the user's name, the next node greets them
    two            two branches each ask, a join waits for both answers
    post <rest>    a node posts a message through the Aion thread, then returns
    note <rest>    a node posts a message through the Aion thread and returns None
    quiet          a node returns None and says nothing
    write <rest>   an agent node drafts, a function node returns the draft
    draft <rest>   an agent node drafts, and its text is the reply

The module also holds an agent node as a variable of its own (``drafter``);
the path in ``aion.yaml`` names no object, and the server takes the
workflow (``root_agent``) before the agents of its module.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events import Event, RequestInput
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.workflow import START, JoinNode, Workflow
from google.genai import types

from aion.adk.authoring.invocation import Thread
from aion.core.runtime import get_aion_runtime_context

from tests.scenarios.agents.adk_workflow import answers

__all__ = ["drafter", "root_agent"]

ROUTES = ("text", "data", "ask", "two", "post", "note", "quiet", "write", "draft")


class DraftLlm(BaseLlm):
    """A model that drafts the text it is given."""

    model: str = "draft"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        user = [content for content in llm_request.contents or [] if content.role == "user"]
        text = "".join(part.text for part in user[-1].parts or [] if part.text) if user else ""
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=answers.written(text))]), partial=False
        )


def route(node_input: str) -> Event:
    """Send the rest of the message to the branch its first word names."""
    word, _, rest = node_input.strip().partition(" ")
    return Event(output=rest, route=word if word in ROUTES else "text")


def shout(node_input: str) -> str:
    return answers.shout(node_input)


def summarize(node_input: str) -> dict:
    return answers.summary(node_input)


async def ask_name(node_input: str) -> AsyncGenerator[Any, None]:
    yield RequestInput(interrupt_id="ask-name", message=answers.NAME_QUESTION, response_schema={"type": "string"})


def greet(node_input: str) -> str:
    return answers.greeting(node_input)


async def ask_first(node_input: str) -> AsyncGenerator[Any, None]:
    yield RequestInput(interrupt_id="ask-first", message=answers.FIRST_QUESTION, response_schema={"type": "string"})


async def ask_second(node_input: str) -> AsyncGenerator[Any, None]:
    yield RequestInput(interrupt_id="ask-second", message=answers.SECOND_QUESTION, response_schema={"type": "string"})


def combine(node_input: dict) -> str:
    return answers.both(node_input["ask_first"], node_input["ask_second"])


async def post(node_input: str) -> str:
    await Thread.from_context(get_aion_runtime_context()).post(answers.POSTED)
    return answers.shout(node_input)


async def note(node_input: str) -> None:
    await Thread.from_context(get_aion_runtime_context()).post(answers.noted(node_input))


def quiet(node_input: str) -> None:
    return None


def finish(node_input: str) -> str:
    return f"final: {node_input}"


drafter = LlmAgent(name="drafter", model=DraftLlm())
"""An agent node, kept as a module variable: the server still serves the workflow."""

joined = JoinNode(name="joined")

root_agent = Workflow(
    name="router",
    edges=[
        (
            START,
            route,
            {
                "text": shout,
                "data": summarize,
                "ask": ask_name,
                "two": (ask_first, ask_second),
                "post": post,
                "note": note,
                "quiet": quiet,
                "write": drafter,
                "draft": LlmAgent(name="final_drafter", model=DraftLlm()),
            },
        ),
        (ask_name, greet),
        ((ask_first, ask_second), joined),
        (joined, combine),
        (drafter, finish),
    ],
)
