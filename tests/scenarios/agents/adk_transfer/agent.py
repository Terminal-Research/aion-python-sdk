"""A router with sub-agents it hands the conversation to (``transfer_to_agent``).

ADK gives an ``LlmAgent`` with sub-agents the ``transfer_to_agent`` tool: its
model calls it, and the named agent answers in the same turn. On the next
message ADK's ``Runner`` goes on with the agent that answered last, as long
as that agent may hand the conversation back to its parent; an agent that
may not (``disallow_transfer_to_parent``) answers its turn, and the next one
starts at the root again.

Each model here decides from the user's latest message alone:

    router    "bill ..."    hands over to ``billing``
              "lock ..."    hands over to ``locked``
              anything else answers itself
    billing   "to support"  hands over to ``support``
              anything else answers itself
    support, locked         answer themselves
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

__all__ = ["BILLING", "LOCKED", "ROUTER", "SUPPORT", "answer", "create_agent"]

ROUTER = "router"
BILLING = "billing"
SUPPORT = "support"
LOCKED = "locked"

TRANSFER_TOOL = "transfer_to_agent"
CONTEXT_PREFIX = "For context:"
"""How ADK begins the text it shows a model in place of another agent's event."""


def answer(agent: str, text: str) -> str:
    """What ``agent``'s model says to the user's message ``text``."""
    return f"{agent}: {text}"


def _latest_user_text(llm_request: LlmRequest) -> str:
    """The user's latest message: the last user text that is not another agent's event retold."""
    for content in reversed(llm_request.contents or []):
        if content.role != "user":
            continue
        texts = [part.text for part in content.parts or [] if part.text]
        if texts and not texts[0].startswith(CONTEXT_PREFIX):
            return "".join(texts)
    return ""


class HandoverLlm(BaseLlm):
    """A model that hands the conversation over on a keyword, and answers itself otherwise."""

    model: str = "handover"
    agent: str
    hands_over: dict[str, str] = {}
    """Keyword the user's message starts with -> the agent that takes the conversation."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _latest_user_text(llm_request)
        for keyword, target in self.hands_over.items():
            if text.startswith(keyword):
                call = types.FunctionCall(name=TRANSFER_TOOL, args={"agent_name": target}, id=f"to-{target}")
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(function_call=call)]), partial=False
                )
                return
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=answer(self.agent, text))]), partial=False
        )


def create_agent() -> LlmAgent:
    """Build the router and the agents it hands the conversation to."""
    return LlmAgent(
        name=ROUTER,
        model=HandoverLlm(agent=ROUTER, hands_over={"bill": BILLING, "lock": LOCKED}),
        sub_agents=[
            LlmAgent(
                name=BILLING,
                description="Answers questions about bills.",
                model=HandoverLlm(agent=BILLING, hands_over={"to support": SUPPORT}),
            ),
            LlmAgent(
                name=SUPPORT,
                description="Helps with technical problems.",
                model=HandoverLlm(agent=SUPPORT),
            ),
            LlmAgent(
                name=LOCKED,
                description="Answers one turn and never hands the conversation back.",
                model=HandoverLlm(agent=LOCKED),
                disallow_transfer_to_parent=True,
            ),
        ],
    )
