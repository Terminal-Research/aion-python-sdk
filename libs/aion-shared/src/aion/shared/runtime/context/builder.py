from __future__ import annotations

from google.protobuf.json_format import MessageToDict
from typing import TYPE_CHECKING, Optional

from aion.shared.constants import (
    DISTRIBUTION_EXTENSION_URI_V1,
    EVENT_EXTENSION_URI_V1,
)
from aion.shared.logging import get_logger
from aion.shared.types.a2a import A2AInbox
from aion.shared.types.a2a.extensions.distribution import DistributionExtensionV1
from .models import AgentIdentity, AionRuntimeContext
from .utils import extract_event

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext

logger = get_logger()


class AionRuntimeContextBuilder:
    """Builds AionRuntimeContext from an A2A RequestContext."""

    @classmethod
    def from_request_context(cls, request_context: Optional["RequestContext"]) -> Optional[AionRuntimeContext]:
        """Return AionRuntimeContext parsed from the request, or None if unavailable."""
        if not request_context:
            return None

        inbox = A2AInbox.from_request_context(request_context)
        if inbox is None:
            return None

        try:
            if DISTRIBUTION_EXTENSION_URI_V1 in inbox.metadata:
                return cls._build_from_distribution(inbox)
            return cls._build_without_distribution(inbox)
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
    def _build_from_distribution(inbox: A2AInbox) -> AionRuntimeContext:
        """Build context with identity and optional event from distribution extension."""
        dist_dict = MessageToDict(inbox.metadata[DISTRIBUTION_EXTENSION_URI_V1])
        dist_ext = DistributionExtensionV1.model_validate(dist_dict)

        has_event = (
                inbox.message is not None
                and EVENT_EXTENSION_URI_V1 in inbox.message.metadata
        )

        try:
            event = extract_event(inbox) if has_event else None
        except Exception as ex:
            logger.warning("Failed to extract event: %s", ex)
            event = None

        try:
            identity = AgentIdentity.from_distribution(dist_ext)
        except Exception as ex:
            logger.warning("Failed to build identity: %s", ex)
            identity = None

        return AionRuntimeContext(inbox=inbox, event=event, identity=identity)

    @staticmethod
    def _build_without_distribution(inbox: A2AInbox) -> AionRuntimeContext:
        """Build minimal context when no distribution extension is present."""
        return AionRuntimeContext(inbox=inbox, event=None, identity=None)
