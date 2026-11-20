"""An ordinary LangGraph tool-calling agent, built the way LangGraph documents it.

``MessagesState``, a node that calls a chat model bound to tools, the
prebuilt ``ToolNode`` and ``tools_condition`` between them. The input is
read from ``state["messages"]`` and the answer is the model's own
``AIMessage``: no ``Thread``, no emitter, no inbox. Whatever reaches the
client got there through the adapter's handling of LangGraph's own output.

The graph is returned uncompiled, so the server compiles it with its
checkpointer - which is what gives the agent memory between turns.

The model is ``native_script`` behind ``BaseChatModel``; see there for
what it answers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any, Optional

from aion.langgraph.authoring import inline_files
from aion.langgraph.authoring.models import _file_inlining_chat_openai
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.messages.content import create_file_block
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode, tools_condition
from langgraph.types import interrupt

from tests.scenarios.agents.native_script import (
    FAILURE_TEXT,
    Forecast,
    forecast_for,
    ModelFailure,
    ModelTurn,
    UserMessage,
    append_progress,
    count_call,
    get_weather,
    model_turn,
    refuse_files,
    ticker,
    tokens,
)

__all__ = [
    "Forecast",
    "ScriptedChatModel",
    "create_async_graph",
    "create_graph",
    "create_prebuilt_agent",
    "create_provider_strategy_agent",
    "create_refused_files_graph",
    "create_tool_strategy_agent",
]


def _parts(message: HumanMessage) -> tuple[dict[str, Any], ...]:
    """The user message's content blocks, as ``inputs`` describes them."""
    content = message.content
    if isinstance(content, str):
        return ({"type": "text", "text": content},)
    described: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            described.append({"type": "text", "text": block})
        elif block.get("type") == "text":
            described.append({"type": "text", "text": block["text"]})
        elif block.get("type") in ("file", "image", "audio", "video"):
            entry: dict[str, Any] = {"type": "file", "mime_type": block.get("mime_type")}
            if block.get("base64") is not None:
                entry["size"] = len(base64.b64decode(block["base64"]))
            if block.get("url") is not None:
                entry["url"] = block["url"]
            described.append(entry)
        else:
            described.append({"type": block.get("type")})
    return tuple(described)


def _media_types(messages: Sequence[BaseMessage]) -> list[Optional[str]]:
    """The media types of the files the messages carry."""
    return [
        block.get("mime_type")
        for message in messages
        if isinstance(message.content, list)
        for block in message.content
        if isinstance(block, dict) and block.get("type") in ("file", "image", "audio", "video")
    ]


def _turn(messages: Sequence[BaseMessage]) -> ModelTurn:
    """Read the conversation the way the script needs it."""
    latest = max(index for index, message in enumerate(messages) if isinstance(message, HumanMessage))
    user = messages[latest]
    return model_turn(
        UserMessage(text=user.text, parts=_parts(user)),
        tool_results=[message.text for message in messages[latest + 1:] if isinstance(message, ToolMessage)],
        earlier_user_texts=[
            message.text for message in messages[:latest] if isinstance(message, HumanMessage)
        ],
    )


def _file_block(turn: ModelTurn) -> dict[str, Any]:
    """The file the answer carries, as a standard block with its content inline."""
    name, media_type, content = turn.file
    return create_file_block(base64=base64.b64encode(content).decode(), mime_type=media_type, filename=name)


def _tool_call(turn: ModelTurn) -> list[dict[str, Any]]:
    if turn.tool_call is None:
        return []
    name, args = turn.tool_call
    return [{"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:8]}", "type": "tool_call"}]


class ScriptedChatModel(BaseChatModel):
    """A chat model that answers from ``native_script``, and streams."""

    model_name: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        """Accept the tools; the script already knows which one it calls."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        refuse_files(_media_types(messages))
        turn = _turn(messages)
        if turn.fail_after is not None:
            raise ModelFailure(FAILURE_TEXT)
        content = [turn.text, _file_block(turn)] if turn.file else turn.text
        message = AIMessage(content=content, tool_calls=_tool_call(turn))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        refuse_files(_media_types(messages))
        turn = _turn(messages)
        for piece in tokens(turn.thought):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=[{"type": "reasoning", "reasoning": piece}])
            )
        for index, piece in enumerate(tokens(turn.text)):
            if turn.fail_after is not None and index == turn.fail_after:
                raise ModelFailure(FAILURE_TEXT)
            if turn.delay:
                await asyncio.sleep(turn.delay)
            append_progress(turn.progress_path, piece)
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
        if turn.file:
            yield ChatGenerationChunk(message=AIMessageChunk(content=[_file_block(turn)]))
        for call in _tool_call(turn):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": 0,
                        }
                    ],
                )
            )


def _answer_text(resume: Any) -> str:
    """The user's answer: what ``interrupt()`` returned for a text reply, the text itself.

    Read strictly, with no fallback, so a change of what the server resumes
    with fails the interrupt scenarios instead of passing unnoticed.
    """
    if not isinstance(resume, str):
        raise TypeError(f"interrupt() returned {type(resume).__name__}, not the text of the reply")
    return resume


def confirm_action(action: str) -> str:
    """Ask the user to confirm an action, and report what they answered.

    Args:
        action: What is about to be done.
    """
    return _answer_text(interrupt(f"Proceed with {action}?"))


def state_files(state: Annotated[dict, InjectedState]) -> str:
    """Describe every file the graph's state holds: its address, and whether its content is inline."""
    return json.dumps(
        [
            {"url": block.get("url"), "inline": "base64" in block}
            for message in state["messages"]
            if isinstance(message.content, list)
            for block in message.content
            if isinstance(block, dict) and block.get("type") in ("file", "image", "audio", "video")
        ]
    )


def _tools() -> list:
    return [tool(get_weather), tool(ticker), tool(confirm_action), tool(count_call), tool(state_files)]


def create_prebuilt_agent():
    """The same agent from ``langchain.agents.create_agent``, as most are built.

    Compiled, and with no checkpointer of its own: the call a guide shows
    first. The server has to supply the checkpointer for it to run at all.
    """
    return create_agent(ScriptedChatModel(), tools=_tools())


class ForecastChatModel(BaseChatModel):
    """A chat model that answers with the forecast, in the form the agent asks for.

    Bound with ``response_format`` - what ``ProviderStrategy`` passes - it
    writes the forecast as JSON text, as a provider's own structured output
    does; bound to the schema's tool - ``ToolStrategy`` - it calls that tool.
    """

    model_name: str = "forecast"
    as_text: bool = False

    @property
    def _llm_type(self) -> str:
        return "forecast"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ForecastChatModel":
        return self.model_copy(update={"as_text": "response_format" in kwargs})

    @staticmethod
    def _forecast(messages: Sequence[BaseMessage]) -> dict[str, Any]:
        user = next(message for message in reversed(messages) if isinstance(message, HumanMessage))
        return forecast_for(user.text)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        forecast = self._forecast(messages)
        if self.as_text:
            message = AIMessage(content=json.dumps(forecast))
        else:
            message = AIMessage(content="", tool_calls=[{"name": "Forecast", "args": forecast, "id": "call-forecast"}])
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        forecast = self._forecast(messages)
        if self.as_text:
            for piece in tokens(json.dumps(forecast)):
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
            return
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": "Forecast", "args": json.dumps(forecast), "id": "call-forecast", "index": 0}
                ],
            )
        )


def create_tool_strategy_agent():
    """A ``create_agent`` agent whose answer is a ``Forecast``, through the schema's tool."""
    return create_agent(ForecastChatModel(), tools=[], response_format=ToolStrategy(Forecast))


def create_provider_strategy_agent():
    """A ``create_agent`` agent whose answer is a ``Forecast``, through the provider's structured output."""
    return create_agent(ForecastChatModel(), tools=[], response_format=ProviderStrategy(Forecast))


def create_graph() -> StateGraph:
    """Build the agent: model, tools, and the loop between them."""
    return _build_graph(inline=False)


async def create_async_graph() -> StateGraph:
    """The same agent from an ``async def`` factory, as one that awaits while it builds - loading MCP tools, say."""
    await asyncio.sleep(0)
    return _build_graph(inline=False)


def create_file_inlining_graph() -> StateGraph:
    """The same agent, sending the files of its messages to the model by their content.

    The one call of Aion's a graph with its own model makes for that:
    ``inline_files`` on the messages it hands the model.
    """
    return _build_graph(inline=True)


def create_refused_files_graph() -> StateGraph:
    """The same agent with its model behind the SDK's model class, as ``aion_chat_openai`` builds it.

    The SDK's model class sends the files of the messages by their content
    and leaves out a file the model refuses, with a note in its place.
    """
    return _build_graph(inline=False, model=_file_inlining_chat_openai(ScriptedChatModel)())


def _build_graph(*, inline: bool, model: Optional[BaseChatModel] = None) -> StateGraph:
    tools = _tools()
    model = (model or ScriptedChatModel()).bind_tools(tools)

    async def call_model(state: MessagesState) -> dict:
        messages = await inline_files(state["messages"]) if inline else state["messages"]
        return {"messages": [await model.ainvoke(messages)]}

    builder = StateGraph(MessagesState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")
    return builder
