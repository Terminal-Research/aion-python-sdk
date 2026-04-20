"""Builds AionContext from RequestContext."""

from aion.shared.logging import get_logger
from aion.shared.types.a2a import A2AInbox
from typing import Optional, TYPE_CHECKING

from ..context import AionContext, AionContextBuilder

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

logger = get_logger()


class RuntimeContextBuilder:
    """Builds AionContext from RequestContext."""

    def __init__(self, request_context: Optional["RequestContext"]) -> None:
        """Initialize builder with a RequestContext.

        Args:
            request_context: A2A RequestContext containing task, message, and metadata.
        """
        self._request_context = request_context

    def build(self) -> Optional[AionContext]:
        """Generate AionContext from RequestContext.

        Returns:
            AionContext if RequestContext and required extensions are available, None otherwise.
        """
        if not self._request_context:
            return None

        inbox = A2AInbox.from_request_context(self._request_context)
        if inbox is None:
            return None

        try:
            return AionContextBuilder.build(inbox=inbox)
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
