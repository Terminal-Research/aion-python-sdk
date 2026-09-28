"""Graph state of the LangGraph scenario agent."""

from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from aion.core.a2a import A2AOutbox
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

__all__ = ["ScenarioState"]


class ScenarioState(TypedDict, total=False):
    """What the router hands to the behaviour node.

    ``messages`` is the SDK's own input key: the server converts the inbound
    A2A message into a HumanMessage under it.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    text: str
    handler: str
    a2a_outbox: Optional[A2AOutbox]
