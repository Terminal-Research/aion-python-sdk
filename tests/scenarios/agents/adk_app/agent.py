"""An ADK ``App`` beside its agent, with a plugin that marks every answer.

The module defines the agent (``root_agent``) and the ``App`` that holds it,
as an ADK project does, and the path in ``aion.yaml`` names neither: the
server takes the App, as ADK's own loader does, and runs the agent with the
App's plugins. The model answers ``echo: <message>``; the plugin's
``after_model_callback`` adds ``MARK`` to it, so an answer that carries the
mark ran with the plugin.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

__all__ = ["MARK", "answer", "app", "root_agent"]

MARK = " (marked by the plugin)"


def answer(text: str) -> str:
    """What the model says to the user's message ``text``, before the plugin marks it."""
    return f"echo: {text}"


class EchoLlm(BaseLlm):
    """A model that repeats the user's latest message."""

    model: str = "echo"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        user = [content for content in llm_request.contents or [] if content.role == "user"]
        text = "".join(part.text for part in user[-1].parts or [] if part.text) if user else ""
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=answer(text))]), partial=False)


class MarkingPlugin(BasePlugin):
    """Adds ``MARK`` to every text the model answers with."""

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        content = llm_response.content
        if content is None or not content.parts or not content.parts[0].text:
            return None
        marked = types.Content(role=content.role, parts=[types.Part(text=content.parts[0].text + MARK)])
        return llm_response.model_copy(update={"content": marked})


root_agent = LlmAgent(name="echo", model=EchoLlm())

app = App(name="echo_app", root_agent=root_agent, plugins=[MarkingPlugin(name="marking")])
