from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from aion.core.logging import get_logger
from aion.core.agent import BaseMessage, User  # noqa: F401 — re-export User

if TYPE_CHECKING:
    pass

logger = get_logger()


class Message(BaseMessage):
    """ADK inbound message bound to its thread."""

    async def react(
            self,
            key: str,
            *,
            operation: Literal["add", "remove"] = "add",
            display_value: Optional[str] = None,
    ) -> None:
        """Express a reaction against the current message.

        Not yet implemented in the ADK authoring runtime.
        Reaction support will be added together with the ADK emit_reaction helper.
        """
        logger.warning(
            "Message.react() is not yet supported in the ADK authoring runtime. "
            "No reaction was sent."
        )
