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
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt

from tests.scenarios.agents.native_script import (
    FAILURE_TEXT,
    ModelFailure,
    ModelTurn,
    UserMessage,
    append_progress,
    count_call,
    get_weather,
    model_turn,
    ticker,
    tokens,
)

__all__ = ["ScriptedChatModel", "create_graph", "create_prebuilt_agent"]


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
        elif block.get("type") == "file":
            entry: dict[str, Any] = {"type": "file", "mime_type": block.get("mime_type")}
            if block.get("base64") is not None:
                entry["size"] = len(base64.b64decode(block["base64"]))
            if block.get("url") is not None:
                entry["url"] = block["url"]
            described.append(entry)
        else:
            described.append({"type": block.get("type")})
    return tuple(described)


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


def _tool_call(turn: ModelTurn) -> list[dict[str, Any]]:
    if turn.tool_call is None:
        return []
    name, args = turn.tool_call
    return [{"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:8]}", "type": "tool_call"}]


class ScriptedChatModel(BaseChatModel):
    """A chat model that answers from ``native_script``, and streams."""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        """Accept the tools; the script already knows which one it calls."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        turn = _turn(messages)
        if turn.fail_after is not None:
            raise ModelFailure(FAILURE_TEXT)
        message = AIMessage(content=turn.text, tool_calls=_tool_call(turn))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
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
    """The user's answer out of what ``interrupt()`` returned.

    The server resumes with ``Command(resume=<the graph input it built from
    the A2A message>)``, so the value is ``{"messages": [HumanMessage]}``
    rather than the answer itself - the form the LangGraph adapter's README
    documents. Read strictly, with no fallback, so a change of that form
    fails the interrupt scenarios instead of passing unnoticed.
    """
    (message,) = resume["messages"]
    return message.text


def confirm_action(action: str) -> str:
    """Ask the user to confirm an action, and report what they answered.

    Args:
        action: What is about to be done.
    """
    return _answer_text(interrupt(f"Proceed with {action}?"))


def _tools() -> list:
    return [tool(get_weather), tool(ticker), tool(confirm_action), tool(count_call)]


def create_prebuilt_agent():
    """The same agent from ``langchain.agents.create_agent``, as most are built.

    Compiled, and with no checkpointer of its own: the call a guide shows
    first. The server has to supply the checkpointer for it to run at all.
    """
    return create_agent(ScriptedChatModel(), tools=_tools())


def create_graph() -> StateGraph:
    """Build the agent: model, tools, and the loop between them."""
    tools = _tools()
    model = ScriptedChatModel().bind_tools(tools)

    async def call_model(state: MessagesState) -> dict:
        return {"messages": [await model.ainvoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")
    return builder
