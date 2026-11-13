"""A ``SequentialAgent`` of two steps; the first asks the user before it answers.

The first step calls ``confirm_action``, ADK's own confirmation: the run
pauses, and the task waits for the user. When the answer arrives the
sequence has to go on where it stopped - the first step answers, and then the
second step runs, as it does under ADK's own ``Runner``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

from tests.scenarios.agents.adk_native.agent import confirm_action

__all__ = ["STEPS", "create_agent", "step_answer"]

AGENT_NAME = "sequence_agent"
STEPS = ("first", "second")
"""The steps, in the order they run."""

CONFIRM_TOOL = "confirm_action"
ANSWERED = ("yes", "no")


def step_answer(step: str, confirmed: str | None = None) -> str:
    """What a step's model says: the first once the user answered, the second right away."""
    return f"{step} done: {confirmed}" if confirmed else f"{step} done"


def _text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), partial=False)


class StepLlm(BaseLlm):
    """A step's model: answers at once, or asks for a confirmation first and answers with it."""

    model: str = "step"
    step: str
    asks: bool = False

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if not self.asks:
            yield _text(step_answer(self.step))
            return
        confirmed = self._confirmation(llm_request)
        if confirmed is not None:
            yield _text(step_answer(self.step, confirmed))
            return
        call = types.FunctionCall(name=CONFIRM_TOOL, args={"action": self.step}, id=f"call-{self.step}")
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]), partial=False)

    @staticmethod
    def _confirmation(llm_request: LlmRequest) -> str | None:
        """What the confirmation tool answered in this turn once the user had replied, or None while it waits.

        Only what follows the user's latest message counts: an earlier turn
        of the context was confirmed for itself.
        """
        contents = llm_request.contents or []
        latest = max(
            (index for index, content in enumerate(contents)
             if content.role == "user" and any(part.text for part in content.parts or [])),
            default=0,
        )
        for content in contents[latest:]:
            for part in content.parts or []:
                response = part.function_response
                if response is not None and response.name == CONFIRM_TOOL:
                    result = str((response.response or {}).get("result", ""))
                    if result in ANSWERED:
                        return result
        return None


def create_agent() -> SequentialAgent:
    """Build the sequence: a step that asks, then a step that answers."""
    first, second = STEPS
    return SequentialAgent(
        name=AGENT_NAME,
        sub_agents=[
            LlmAgent(name=first, model=StepLlm(step=first, asks=True), tools=[confirm_action]),
            LlmAgent(name=second, model=StepLlm(step=second)),
        ],
    )
