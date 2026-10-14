"""The toolkit host: toolkit plugins found, admitted, run and translated into A2A.

A toolkit is an ``aion.core.toolkit.Toolkit`` registered under the
``aion.toolkits`` entry point group. ``load_toolkit_handlers`` finds the
installed ones at startup and registers their extensions; a
``ToolkitTaskHandler`` serves the tasks of one, translating what it yields
with a ``TurnTranslator``. See ``docs/development/toolkit-host.md``.
"""

from .discovery import load_toolkit_handlers
from .handler import (
    CANCEL_GRACE_SECONDS,
    ToolkitAdmission,
    ToolkitTaskHandler,
    refusal_data,
)
from .secret_values import SecretMasker, deployment_secret_values
from .translation import (
    MAX_ARTIFACT_TEXT_LENGTH,
    MAX_DURABLE_MESSAGES_PER_TURN,
    MAX_MESSAGE_TEXT_LENGTH,
    ToolkitProtocolError,
    TurnTranslator,
)

__all__ = [
    "CANCEL_GRACE_SECONDS",
    "MAX_ARTIFACT_TEXT_LENGTH",
    "MAX_DURABLE_MESSAGES_PER_TURN",
    "MAX_MESSAGE_TEXT_LENGTH",
    "SecretMasker",
    "ToolkitAdmission",
    "ToolkitProtocolError",
    "ToolkitTaskHandler",
    "TurnTranslator",
    "deployment_secret_values",
    "load_toolkit_handlers",
    "refusal_data",
]
