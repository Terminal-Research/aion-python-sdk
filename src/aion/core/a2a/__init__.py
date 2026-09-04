"""Core A2A package — base model, enums, artifact builders, and extension types.

Re-exports all A2A types used across the SDK from a single stable import path.
"""

from a2a._base import A2ABaseModel

from .artifacts import data_artifact, file_artifact, url_artifact

from .enums import (
    A2AEventType,
    A2AMetadataKey,
    ArtifactId,
    ArtifactName,
    ArtifactStreamingStatus,
    ArtifactStreamingStatusReason,
    MessageType,
)

from .models import (
    A2AInbox,
    A2AManifest,
    A2AOutbox,
    ContextsList,
    Conversation,
    ConversationTaskStatus,
)

from .request import (
    GetContextRequest,
    GetContextsListRequest,
)

from .request_params import (
    GetContextParams,
    GetContextsListParams,
)

from .response import (
    GetContextSuccessResponse,
    GetContextsListSuccessResponse,
)

from .extensions.cards import CardActionEventPayload
from .extensions.distribution import (
    Behavior,
    Distribution,
    DistributionExtensionV1,
    Environment,
    Identity,
    PrincipalIdentity,
    ServiceIdentity,
)
from .extensions.event import EventMessageMetadataV1, EventPartMetadataV1
from .extensions.messaging import (
    CommandEventPayload,
    MessageActionPayload,
    MessageActionTrajectory,
    MessageEventPayload,
    MessageEventTrajectory,
    ReactionActionPayload,
    ReactionEventPayload,
    SourceSystemEventPayload,
)
from .extensions.traceability import TraceStateEntry, TraceabilityExtensionV1
