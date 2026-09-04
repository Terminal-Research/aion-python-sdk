"""Builder that constructs AionRuntimeContext from an A2A RequestContext.

Parses distribution, event, and identity data from the inbound A2A
message metadata into a typed AionRuntimeContext object.
"""

from __future__ import annotations
import logging

from typing import TYPE_CHECKING, Optional

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.core.a2a import A2AInbox
from .extensions import (
    ExtensionActivationError,
    AionRuntimeExtensions,
    aion_a2a_extension_registry,
)
from .models import AionRuntimeContext

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

logger = logging.getLogger(__name__)


class AionRuntimeContextBuilder:
    """Builds AionRuntimeContext from an A2A RequestContext."""

    @classmethod
    def from_request_context(cls, request_context: Optional["RequestContext"]) -> Optional[AionRuntimeContext]:
        """Return AionRuntimeContext parsed from the request, or None if unavailable.

        Raises:
            ExtensionActivationError: an active extension is missing a
                required co-activated extension, or its payload is missing
                or malformed. Unlike the "no context available" cases below,
                this is a malformed request and must not be swallowed into a
                silent None - the caller should reject the request.
        """
        if not request_context:
            return None

        try:
            extensions = AionRuntimeExtensions.collect(
                request_context, aion_a2a_extension_registry.get_all()
            )

            inbox = A2AInbox.from_request_context(request_context)
            if inbox is None:
                return None

            return cls._build(inbox, extensions)
        except ExtensionActivationError:
            raise
        except (KeyError, AttributeError) as e:
            logger.debug(
                "AionRuntimeContext not available: %s. "
                "Graphs without a2a_inbox don't need runtime context.",
                type(e).__name__,
            )
            return None
        except Exception as e:
            logger.exception("Failed to build AionRuntimeContext: %s", e)
            return None

    @staticmethod
    def _build(inbox: A2AInbox, extensions: AionRuntimeExtensions) -> AionRuntimeContext:
        """Build context, reading payloads off extensions.

        Both the distribution extension payload and the event are produced by the
        collect/verify pipeline (TaskMetadataCollector and MessagesCollector
        respectively) - no second parse here.

        Args:
            inbox: A2A inbox.
            extensions: Verified, per-request set of active,
                registered extensions from the collect/verify pipeline.

        Returns:
            Runtime context with the event and distribution extension payload, if any.
        """
        return AionRuntimeContext(
            inbox=inbox,
            event=extensions.first_event(),
            distribution_extension_payload=extensions.get(DISTRIBUTION_EXTENSION_URI_V1),
            extensions=extensions,
        )
