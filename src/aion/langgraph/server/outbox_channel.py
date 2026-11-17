"""An outbox field declared as an ordinary state field, kept out of the checkpoint.

``A2AOutboxField`` puts a graph's outbox on LangGraph's ``UntrackedValue``
channel: the value exists only during the run that wrote it, and the
checkpoint holds no ``A2AOutbox``. A field declared as ``Optional[A2AOutbox]``
or ``A2AOutbox`` gets LangGraph's ordinary ``LastValue`` channel instead, which
checkpoints the object; LangGraph's serializer does not know its class and
warns, on every load, that it will refuse to load it.

The server gives such a field the channel ``A2AOutboxField`` declares, in the
graph it serves, so the field behaves as ``A2AOutboxField`` there. The server
reads the outbox from the run's updates, never from the state, so the turn
is answered as before. A field with a reducer of its own is left alone, and
so is a graph the server does not compile or copy itself: the graph of a
subgraph node, or a graph run outside the server.
"""

from __future__ import annotations

import logging
import types
from typing import Annotated, Any, Optional, Union, get_args, get_origin

from langgraph.channels import LastValue, UntrackedValue

from aion.core.a2a import A2AOutbox

__all__ = ["untracked_outbox_channels"]

logger = logging.getLogger(__name__)


def untracked_outbox_channels(graph: Any) -> Optional[dict[str, Any]]:
    """The channels of ``graph`` with every ordinary outbox channel replaced by an untracked one.

    None when the graph has no such channel, or no channels LangGraph names
    in a ``channels`` dict: a ``StateGraph`` and a compiled graph have them.
    """
    channels = getattr(graph, "channels", None)
    if not isinstance(channels, dict):
        return None
    replaced = {
        key: UntrackedValue(channel.typ, channel.key)
        for key, channel in channels.items()
        if _is_ordinary_outbox_channel(channel)
    }
    if not replaced:
        return None
    for key in replaced:
        logger.info(
            "State field %r holds an A2AOutbox on an ordinary channel; the server keeps it out of the "
            "checkpoint, as A2AOutboxField does. Declare the field as A2AOutboxField to have the same "
            "outside the server.",
            key,
        )
    return {**channels, **replaced}


def _is_ordinary_outbox_channel(channel: Any) -> bool:
    """Whether ``channel`` is LangGraph's plain ``LastValue`` of an ``A2AOutbox``, optional or not."""
    return type(channel) is LastValue and _is_outbox_type(channel.typ)


def _is_outbox_type(typ: Any) -> bool:
    if get_origin(typ) is Annotated:
        typ = get_args(typ)[0]
    if typ is A2AOutbox:
        return True
    if get_origin(typ) in (Union, types.UnionType):
        return set(get_args(typ)) == {A2AOutbox, type(None)}
    return False
