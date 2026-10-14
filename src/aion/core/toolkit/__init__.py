"""The protocol between the Aion server and toolkit plugins.

A toolkit is a framework-agnostic package that does one kind of work (for
example, evolving an agent's repository) and plugs into any Aion server: it
registers a :class:`Toolkit` object under the ``aion.toolkits`` entry point
group, and the server routes every request that activates the toolkit's A2A
extension to it. The toolkit yields protocol values — messages, artifacts,
status details and one result per turn — and the server turns them into A2A
tasks, events and errors. See :mod:`aion.core.toolkit.plugin` for the plugin
contract, :mod:`aion.core.toolkit.models` for the values and
:mod:`aion.core.toolkit.codes` for the error codes the server reserves.
"""

from aion.core.exceptions import ToolkitRejection

from . import codes
from .models import (
    ARTIFACT_VERSION_KEY,
    Artifact,
    DataPart,
    FilePart,
    Message,
    Metadata,
    Part,
    TextPart,
    ToolkitArtifactEvent,
    ToolkitError,
    ToolkitEvent,
    ToolkitMessageEvent,
    ToolkitOutput,
    ToolkitResult,
    ToolkitState,
    ToolkitStatusEvent,
)
from .plugin import (
    CANCEL,
    ENTRY_POINT_GROUP,
    PROTOCOL_VERSION,
    SHUTDOWN,
    ContextArtifact,
    ModelServiceAccess,
    PreflightContext,
    Toolkit,
    ToolkitContext,
    ToolkitDescriptor,
    ToolkitRequest,
    protocol_compatible,
)

__all__ = [
    "ARTIFACT_VERSION_KEY",
    "Artifact",
    "CANCEL",
    "ContextArtifact",
    "DataPart",
    "ENTRY_POINT_GROUP",
    "FilePart",
    "Message",
    "Metadata",
    "ModelServiceAccess",
    "PROTOCOL_VERSION",
    "Part",
    "PreflightContext",
    "SHUTDOWN",
    "TextPart",
    "Toolkit",
    "ToolkitArtifactEvent",
    "ToolkitContext",
    "ToolkitDescriptor",
    "ToolkitError",
    "ToolkitEvent",
    "ToolkitMessageEvent",
    "ToolkitOutput",
    "ToolkitRejection",
    "ToolkitRequest",
    "ToolkitResult",
    "ToolkitState",
    "ToolkitStatusEvent",
    "codes",
    "protocol_compatible",
]
