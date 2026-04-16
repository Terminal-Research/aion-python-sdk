"""Builds AionContext from RequestContext with integrated callbacks."""

from aion.shared.logging import get_logger
from aion.shared.types.a2a import A2AInbox
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.config import get_stream_writer
from typing import Optional, TYPE_CHECKING

from ..context import AionContext, AionContextBuilder
from ..stream import emit_message

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

logger = get_logger()


class RuntimeContextBuilder:
    """Builds AionContext from RequestContext with integrated request-scoped callbacks."""

    def __init__(self, request_context: Optional["RequestContext"]) -> None:
        """Initialize builder with a RequestContext.

        Args:
            request_context: A2A RequestContext containing task, message, and metadata.
        """
        self._request_context = request_context

    def build(self) -> Optional[AionContext]:
        """Generate AionContext with callback handlers.

        Returns:
            AionContext if RequestContext and required extensions are available, None otherwise.
        """
        if not self._request_context:
            return None

        inbox = A2AInbox.from_request_context(self._request_context)
        if inbox is None:
            return None

        try:
            return AionContextBuilder.build(
                inbox=inbox,
                reply_fn=self._reply_fn,
                history_fn=self._history_fn,
                typing_fn=self._typing_fn,
            )
        except (KeyError, AttributeError) as e:
            # Missing required extension or metadata access error
            # This is normal for graphs that don't declare a2a_inbox in their state schema
            logger.debug(
                "AionContext not available: %s. "
                "Graphs without a2a_inbox don't need runtime context.",
                type(e).__name__,
            )
            return None
        except Exception as e:
            logger.warning("Failed to build AionContext: %s", e)
            return None

    @staticmethod
    async def _reply_fn(content, *, metadata=None) -> None:
        """Write a reply into the current stream via LangGraph StreamWriter."""
        writer = get_stream_writer()
        if isinstance(content, str):
            emit_message(writer, AIMessageChunk(content=content))

        else:
            logger.warning(
                "Thread.reply() received unsupported content type: %s. "
                "Only plain text is supported in this version.",
                type(content).__name__,
            )

    @staticmethod
    async def _typing_fn() -> None:
        """Emit an ephemeral typing indicator into the current stream."""
        writer = get_stream_writer()
        emit_message(writer, AIMessage(content="..."), ephemeral=True)

    @staticmethod
    async def _history_fn(*, limit: int = 20, before=None) -> list:
        """Get thread history.

        TODO: implement via aion-api-client control plane API.
        """
        logger.warning(
            "Thread.history() is not yet implemented. "
            "Returning empty list. "
            "TODO: implement via aion-api-client control plane API."
        )
        return []
