"""A2A extension constants and schema URIs.

Centralized definitions for A2A extension identifiers and payloads per spec.
See: https://docs.aion.to/a2a/extensions
"""

__all__ = [
    "CRON_EXTENSION_URI_V1",
    "WELCOME_MESSAGE_EXTENSION_URI_V1",
    "WELCOME_REQUEST_PAYLOAD_SCHEMA_V1",
    # Distribution extension
    "DISTRIBUTION_EXTENSION_URI_V1",
    "DISTRIBUTION_NOTICE_PAYLOAD_SCHEMA_V1",
    # Cards extension
    "CARDS_EXTENSION_URI_V1",
    "CARDS_PAYLOAD_SCHEMA_V1",
    "CARDS_MEDIA_TYPE",
    "CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1",
    # Event extension
    "EVENT_EXTENSION_URI_V1",
    # Event type URIs (CloudEvents `type` field)
    "MESSAGE_EVENT_TYPE_V1",
    "REACTION_EVENT_TYPE_V1",
    "COMMAND_EVENT_TYPE_V1",
    "CARD_ACTION_EVENT_TYPE_V1",
    # Messaging extension
    "MESSAGING_EXTENSION_URI_V1",
    "MESSAGE_EVENT_PAYLOAD_SCHEMA_V1",
    "REACTION_EVENT_PAYLOAD_SCHEMA_V1",
    "COMMAND_EVENT_PAYLOAD_SCHEMA_V1",
    "SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1",
    "MESSAGE_ACTION_PAYLOAD_SCHEMA_V1",
    "FILE_ACTION_PAYLOAD_SCHEMA_V1",
    "REACTION_ACTION_PAYLOAD_SCHEMA_V1",
    "STREAM_DELTA_PAYLOAD_SCHEMA_V1",
    # Traceability extension
    "TRACEABILITY_EXTENSION_URI_V1",
    # Usage attribution extension
    "USAGE_ATTRIBUTION_EXTENSION_URI_V1",
    "AION_USAGE_ATTRIBUTION_HEADER",
    # Daemon extension
    "DAEMON_EXTENSION_URI_V1",
    # Context extension
    "CONTEXT_EXTENSION_URI_V1",
    "CONTEXT_HTTP_PATHS",
    # Behavior evolution extension
    "BEHAVIOR_EVOLUTION_EXTENSION_URI_V1",
]

# Scheduled invocation provenance (message content remains in ordinary parts).
CRON_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/cron/1.0.0"

# Distribution extension
# See: https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0
DISTRIBUTION_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0"
DISTRIBUTION_NOTICE_PAYLOAD_SCHEMA_V1 = f"{DISTRIBUTION_EXTENSION_URI_V1}#NoticePayload"

# Cards extension (for JSX-like card documents)
# See: https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0
CARDS_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/distribution/cards/1.0.0"
CARDS_PAYLOAD_SCHEMA_V1 = f"{CARDS_EXTENSION_URI_V1}#CardPayload"
CARDS_MEDIA_TYPE = "application/vnd.aion.card+jsx"
CARD_ACTION_EVENT_PAYLOAD_SCHEMA_V1 = f"{CARDS_EXTENSION_URI_V1}#CardActionEventPayload"

# Event extension
# See: https://docs.aion.to/a2a/extensions/aion/event/1.0.0
EVENT_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/event/1.0.0"

# Event type URIs (CloudEvents `type` field values)
MESSAGE_EVENT_TYPE_V1 = "to.aion.distribution.message.1.0.0"
REACTION_EVENT_TYPE_V1 = "to.aion.distribution.reaction.1.0.0"
COMMAND_EVENT_TYPE_V1 = "to.aion.distribution.command.1.0.0"
CARD_ACTION_EVENT_TYPE_V1 = "to.aion.distribution.card-action.1.0.0"

# Messaging extension
# See: https://docs.aion.to/a2a/extensions/aion/distribution/messaging/1.0.0
MESSAGING_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/distribution/messaging/1.0.0"
MESSAGE_EVENT_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#MessageEventPayload"
REACTION_EVENT_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#ReactionEventPayload"
COMMAND_EVENT_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#CommandEventPayload"
SOURCE_SYSTEM_EVENT_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#SourceSystemEventPayload"
MESSAGE_ACTION_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#MessageActionPayload"
FILE_ACTION_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#FileActionPayload"
REACTION_ACTION_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#ReactionActionPayload"
STREAM_DELTA_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#StreamDeltaPayload"

# Traceability extension (W3C trace context)
# See: https://docs.aion.to/a2a/extensions/aion/traceability/1.0.0
TRACEABILITY_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/traceability/1.0.0"

# Opaque signed usage attribution propagated by Aion-managed runtimes.
USAGE_ATTRIBUTION_EXTENSION_URI_V1 = (
    "https://docs.aion.to/a2a/extensions/aion/usage-attribution/1.0.0"
)
AION_USAGE_ATTRIBUTION_HEADER = "Aion-Usage-Attribution"

# Daemon extension (authenticated, environment-scoped daemon interaction)
# See: https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0
DAEMON_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0"

# Context extension (GetContexts, GetContext and DeleteContext)
# See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
CONTEXT_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/context/1.0.0"
CONTEXT_HTTP_PATHS: dict[str, str] = {
    "contexts:get": "GetContexts",
    "context:get": "GetContext",
    "context:delete": "DeleteContext",
}
"""HTTP+JSON path, relative to the A2A endpoint, for each Context method."""

# Behavior evolution extension (an executable extension: the evolution
# toolkit runs the task in place of the agent's framework)
# See: https://docs.aion.to/a2a/extensions/aion/behavior/evolution/1.0.0
BEHAVIOR_EVOLUTION_EXTENSION_URI_V1 = (
    "https://docs.aion.to/a2a/extensions/aion/behavior/evolution/1.0.0"
)

# Optional agent-generated opening message; Voice keeps its configured greeting.
WELCOME_MESSAGE_EXTENSION_URI_V1 = (
    "https://docs.aion.to/a2a/extensions/aion/welcome-message/1.0.0"
)
WELCOME_REQUEST_PAYLOAD_SCHEMA_V1 = (
    f"{WELCOME_MESSAGE_EXTENSION_URI_V1}#WelcomeRequestPayload"
)
