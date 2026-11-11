"""An ordinary ADK agent, built the way ADK documents it.

An ``LlmAgent`` with a plain Python function as its tool. The input is the
invocation's ``user_content`` as ADK assembles it into the model request,
and the answer is the model's own response: no ``Thread``, no emitter, no
inbox. ADK runs the function call, feeds the response back and calls the
model again; the session carries the conversation between turns.

The model is ``native_script`` behind ``BaseLlm``, streaming the way
``BaseLlm.generate_content_async`` describes SSE: partial chunks, then one
aggregated response. See ``native_script`` for what it answers.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from aion.adk.authoring import inline_files_before_model
from fastapi.openapi.models import APIKey, APIKeyIn
from google.adk.agents import LlmAgent
from google.adk.auth.auth_credential import AuthCredential, AuthCredentialTypes
from google.adk.auth.auth_tool import AuthConfig
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.tools import ToolContext
from google.genai import types

from tests.scenarios.agents.native_script import (
    FAILURE_TEXT,
    REPORT_FILENAME,
    ModelFailure,
    ModelTurn,
    UserMessage,
    append_progress,
    count_call,
    get_weather,
    model_turn,
    report_text,
    ticker,
    tokens,
)

__all__ = ["ScriptedLlm", "create_agent", "create_file_inlining_agent"]

AGENT_NAME = "native_agent"

LAST_ANSWER_KEY = "last_answer"
NOTE_KEY = "note"

INSTRUCTION = (
    "Answer the user. Use get_weather for the weather. "
    f"Last answer: {{{LAST_ANSWER_KEY}?}}. Note: {{{NOTE_KEY}?}}."
)
"""ADK fills the ``{key?}`` placeholders from session state on every call,
which is how a scenario can see what ``output_key`` and a tool's state write
left there: ``instruction`` has the model answer with what it received."""


def _is_user_turn(content: types.Content) -> bool:
    """A message the user wrote, rather than a function response ADK sends as "user"."""
    return content.role == "user" and any(
        part.function_response is None for part in content.parts or []
    )


def _text(content: types.Content) -> str:
    return "".join(part.text or "" for part in content.parts or [] if not part.thought)


def _parts(content: types.Content) -> tuple[dict[str, Any], ...]:
    """The user message's parts, as ``inputs`` describes them."""
    described: list[dict[str, Any]] = []
    for part in content.parts or []:
        if part.text is not None:
            described.append({"type": "text", "text": part.text})
        elif part.inline_data is not None:
            described.append(
                {
                    "type": "file",
                    "mime_type": part.inline_data.mime_type,
                    "size": len(part.inline_data.data or b""),
                }
            )
        elif part.file_data is not None:
            described.append(
                {"type": "file", "mime_type": part.file_data.mime_type, "url": part.file_data.file_uri}
            )
        else:
            described.append({"type": "other"})
    return tuple(described)


def _turn(request: LlmRequest) -> ModelTurn:
    """Read the request's contents the way the script needs them."""
    contents = request.contents or []
    latest = max(index for index, content in enumerate(contents) if _is_user_turn(content))
    user = contents[latest]
    results = [
        str((part.function_response.response or {}).get("result", ""))
        for content in contents[latest + 1:]
        for part in content.parts or []
        if part.function_response is not None
    ]
    system = request.config.system_instruction if request.config else None
    return model_turn(
        UserMessage(text=_text(user), parts=_parts(user)),
        tool_results=results,
        earlier_user_texts=[_text(content) for content in contents[:latest] if _is_user_turn(content)],
        instruction=system if isinstance(system, str) else "",
    )


def _response(parts: list[types.Part], *, partial: bool) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=parts), partial=partial)


class ScriptedLlm(BaseLlm):
    """A model that answers from ``native_script``, streaming under SSE."""

    model: str = "scripted"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        turn = _turn(llm_request)
        final: list[types.Part] = []
        if turn.thought:
            final.append(types.Part(text=turn.thought, thought=True))
        if stream:
            for piece in tokens(turn.thought):
                yield _response([types.Part(text=piece, thought=True)], partial=True)
            for index, piece in enumerate(tokens(turn.text)):
                if turn.fail_after is not None and index == turn.fail_after:
                    raise ModelFailure(FAILURE_TEXT)
                if turn.delay:
                    await asyncio.sleep(turn.delay)
                append_progress(turn.progress_path, piece)
                yield _response([types.Part(text=piece)], partial=True)
        elif turn.fail_after is not None:
            raise ModelFailure(FAILURE_TEXT)
        if turn.text:
            final.append(types.Part(text=turn.text))
        if turn.tool_call is not None:
            name, args = turn.tool_call
            final.append(
                types.Part(
                    function_call=types.FunctionCall(
                        name=name, args=args, id=f"call-{uuid.uuid4().hex[:8]}"
                    )
                )
            )
        if turn.error is not None:
            # As ADK's own models end such a response: what was said so far,
            # if anything, with the error beside it.
            code, message = turn.error
            yield LlmResponse(
                content=types.Content(role="model", parts=final) if final else None,
                error_code=code,
                error_message=message,
                partial=False,
            )
            return
        yield _response(final, partial=False)


async def save_report(title: str, tool_context: ToolContext) -> str:
    """Save a report as an artifact.

    Args:
        title: What the report is about.
    """
    version = await tool_context.save_artifact(REPORT_FILENAME, types.Part(text=report_text(title)))
    return f"saved version {version}"


def remember(note: str, tool_context: ToolContext) -> str:
    """Remember a note in session state.

    Args:
        note: What to remember.
    """
    tool_context.state[NOTE_KEY] = note
    return "remembered"


def confirm_action(action: str, tool_context: ToolContext) -> str:
    """Ask the user to confirm an action, and report what they answered.

    ADK's own confirmation: the first call asks and the run pauses; ADK
    calls the tool again with the user's answer once it arrives.

    Args:
        action: What is about to be done.
    """
    confirmation = tool_context.tool_confirmation
    if confirmation is None:
        tool_context.request_confirmation(hint=f"Proceed with {action}?")
        return "waiting for confirmation"
    return "yes" if confirmation.confirmed else "no"


def sign_in(service: str, tool_context: ToolContext) -> str:
    """Sign in to a service, asking the user for its key.

    Args:
        service: The service to sign in to.
    """
    tool_context.request_credential(
        AuthConfig(
            auth_scheme=APIKey(**{"in": APIKeyIn.header, "name": "X-Api-Key"}),
            raw_auth_credential=AuthCredential(auth_type=AuthCredentialTypes.API_KEY, api_key=""),
        )
    )
    return f"waiting for a key to {service}"


def create_agent() -> LlmAgent:
    """Build the agent: a model, its tools, and an answer kept in state."""
    return _build_agent()


def create_file_inlining_agent() -> LlmAgent:
    """The same agent, sending the files of a request to the model by their content.

    The one thing of Aion's an agent with its own model needs for that: the
    ``inline_files_before_model`` callback.
    """
    return _build_agent(before_model_callback=inline_files_before_model)


def _build_agent(**kwargs: Any) -> LlmAgent:
    return LlmAgent(
        name=AGENT_NAME,
        model=ScriptedLlm(),
        instruction=INSTRUCTION,
        tools=[get_weather, ticker, save_report, remember, count_call, confirm_action, sign_in],
        output_key=LAST_ANSWER_KEY,
        **kwargs,
    )
