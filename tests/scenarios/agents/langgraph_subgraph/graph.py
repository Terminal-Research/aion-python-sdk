"""A graph whose work happens in subgraphs, one or two at once.

That is the shape a supervisor with sub-agents has, and any graph composed of
graphs. What runs inside a subgraph has to reach the client as it would
from the parent: a model call streams, and the Aion authoring calls deliver.

The latest user message says which road the turn takes:

    reply <text>    the subgraph: a node thinks ``THOUGHT`` and replies with
                    <text> through the thread, writing nothing into the
                    graph's state
    both-graphs     two subgraphs at once, each with a model that streams
                    ``branch_answer`` of its own name
    both            the same two models at once, as plain nodes of the parent
    anything else   the subgraph: its model node answers from ``native_script``

Two models streaming at once is what a fan-out does - ``Send``, parallel
edges, sub-agents working side by side. Their chunks reach the adapter
interleaved, and each answer still has to arrive whole.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aion.core.runtime import AionRuntimeContext
from aion.langgraph.authoring.invocation import Thread, emit_thinking
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.runtime import Runtime

from tests.scenarios.agents.langgraph_native.graph import ScriptedChatModel
from tests.scenarios.agents.native_script import (
    BRANCH_DELAY_SECONDS,
    BRANCHES,
    THOUGHT,
    branch_answer,
    tokens,
)

__all__ = ["create_graph"]

REPLY_COMMAND = "reply"
BOTH_GRAPHS_COMMAND = "both-graphs"
BOTH_NODES_COMMAND = "both"

MODEL_NODE = "model"
REPLY_NODE = "reply"
SUBGRAPH_NODE = "worker"


def _latest_user_text(messages: list[BaseMessage]) -> str:
    latest = next(message for message in reversed(messages) if isinstance(message, HumanMessage))
    if isinstance(latest.content, str):
        return latest.content
    return "".join(
        block.get("text", "")
        for block in latest.content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _command(state: MessagesState) -> str:
    return _latest_user_text(state["messages"]).strip().partition(" ")[0]


def _route(state: MessagesState) -> str:
    return REPLY_NODE if _command(state) == REPLY_COMMAND else MODEL_NODE


class BranchModel(BaseChatModel):
    """A model that streams its branch's answer a word at a time, slowly enough to interleave."""

    branch: str

    @property
    def _llm_type(self) -> str:
        return "branch"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=branch_answer(self.branch)))])

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for piece in tokens(branch_answer(self.branch)):
            await asyncio.sleep(BRANCH_DELAY_SECONDS)
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))


def _branch_node(branch: str):
    async def call_branch(state: MessagesState) -> dict:
        return {"messages": [await BranchModel(branch=branch).ainvoke(state["messages"])]}

    return call_branch


def _branch_graph(branch: str):
    builder = StateGraph(MessagesState)
    builder.add_node(MODEL_NODE, _branch_node(branch))
    builder.add_edge(START, MODEL_NODE)
    builder.add_edge(MODEL_NODE, END)
    return builder.compile()


def _fan_out(state: MessagesState) -> list[str]:
    command = _command(state)
    if command == BOTH_GRAPHS_COMMAND:
        return [f"{branch}_graph" for branch in BRANCHES]
    if command == BOTH_NODES_COMMAND:
        return [f"{branch}_node" for branch in BRANCHES]
    return [SUBGRAPH_NODE]


async def call_model(state: MessagesState) -> dict:
    return {"messages": [await ScriptedChatModel().ainvoke(state["messages"])]}


async def reply(state: MessagesState, *, runtime: Runtime[AionRuntimeContext]) -> None:
    text = _latest_user_text(state["messages"]).strip().partition(" ")[2]
    emit_thinking(runtime.stream_writer, THOUGHT)
    await Thread.from_context(runtime.context).reply(text)


def _subgraph():
    builder = StateGraph(MessagesState, context_schema=AionRuntimeContext)
    builder.add_node(MODEL_NODE, call_model)
    builder.add_node(REPLY_NODE, reply)
    builder.add_conditional_edges(START, _route, [MODEL_NODE, REPLY_NODE])
    builder.add_edge(MODEL_NODE, END)
    builder.add_edge(REPLY_NODE, END)
    return builder.compile()


def create_graph() -> StateGraph:
    """Build the parent graph around the subgraph."""
    builder = StateGraph(MessagesState, context_schema=AionRuntimeContext)
    builder.add_node(SUBGRAPH_NODE, _subgraph())
    roads = [SUBGRAPH_NODE]
    for branch in BRANCHES:
        builder.add_node(f"{branch}_graph", _branch_graph(branch))
        builder.add_node(f"{branch}_node", _branch_node(branch))
        roads += [f"{branch}_graph", f"{branch}_node"]
    builder.add_conditional_edges(START, _fan_out, roads)
    for road in roads:
        builder.add_edge(road, END)
    return builder
