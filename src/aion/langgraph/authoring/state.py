"""Graph state fields the server reads."""

from __future__ import annotations

from typing import Annotated, Optional

from langgraph.channels import UntrackedValue

from aion.core.a2a import A2AOutbox

__all__ = ["A2AOutboxField"]

A2AOutboxField = Annotated[Optional[A2AOutbox], UntrackedValue(Optional[A2AOutbox])]
"""The type of a graph's ``a2a_outbox`` state field.

The server reads the outbox from the updates of the run whose node wrote
it, never from the checkpoint. On LangGraph's ``UntrackedValue`` channel the
value is not checkpointed at all: the next turn does not see the last one's
outbox in its state, and the checkpoint holds no ``A2AOutbox``, a type
LangGraph's serializer does not know and warns it will refuse to load.

Example:
    from typing import TypedDict

    from aion.langgraph.authoring import A2AOutboxField

    class State(TypedDict):
        messages: Annotated[list[BaseMessage], add_messages]
        a2a_outbox: A2AOutboxField
"""
