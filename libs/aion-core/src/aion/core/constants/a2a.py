"""A2A extension constants and schema URIs.

Centralized definitions for A2A extension identifiers and payloads per spec.
See: https://docs.aion.to/a2a/extensions
"""

__all__ = [
    # Distribution extension
    "DISTRIBUTION_EXTENSION_URI_V1",
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
    "REACTION_ACTION_PAYLOAD_SCHEMA_V1",
    "STREAM_DELTA_PAYLOAD_SCHEMA_V1",
    # Traceability extension
    "TRACEABILITY_EXTENSION_URI_V1",
    # Reflection extension (behaviour evolution)
    "BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1",
    "BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1",
    "BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1",
    "BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1",
    "BEHAVIOUR_EVOLUTION_VERDICT_EVENT_PAYLOAD_SCHEMA_V1",
    "BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1",
    "BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1",
    "BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1",
    "BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1",
    # Daemon extension
    "DAEMON_EXTENSION_URI_V1",
    # Context extensions
    "GET_CONTEXT_EXTENSION_URI_V1",
    "GET_CONTEXTS_LIST_EXTENSION_URI_V1",
]

# Distribution extension
# See: https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0
DISTRIBUTION_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0"

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
REACTION_ACTION_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#ReactionActionPayload"
STREAM_DELTA_PAYLOAD_SCHEMA_V1 = f"{MESSAGING_EXTENSION_URI_V1}#StreamDeltaPayload"

# Traceability extension (W3C trace context)
# See: https://docs.aion.to/a2a/extensions/aion/traceability/1.0.0
TRACEABILITY_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/traceability/1.0.0"

# Reflection extension (behaviour evolution: improver directive/verdict/result payloads)
# See: https://docs.aion.to/a2a/extensions/aion/behaviour/evolution/1.0.0
BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/behaviour/evolution/1.0.0"
BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_TYPE_V1 = "to.aion.behaviour.evolution.directive.1.0.0"
BEHAVIOUR_EVOLUTION_VERDICT_EVENT_TYPE_V1 = "to.aion.behaviour.evolution.verdict.1.0.0"
BEHAVIOUR_EVOLUTION_DIRECTIVE_EVENT_PAYLOAD_SCHEMA_V1 = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#EvolutionDirectiveEventPayload"
BEHAVIOUR_EVOLUTION_VERDICT_EVENT_PAYLOAD_SCHEMA_V1 = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#EvolutionVerdictEventPayload"
BEHAVIOUR_EVOLUTION_RESULT_ACTION_PAYLOAD_SCHEMA_V1 = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#EvolutionResultActionPayload"
# Progress-event payloads streamed while a run is in flight (schema-tagged data
# parts on WORKING status messages, not CloudEvents — no EVENT_TYPE). The
# distributor reacts to these programmatically; they are streamed to the client
# but not persisted in task history (see the improver's events.py).
BEHAVIOUR_EVOLUTION_COMMAND_STARTED_PAYLOAD_SCHEMA_V1 = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#EvolutionCommandStartedPayload"
BEHAVIOUR_EVOLUTION_COMMAND_COMPLETED_PAYLOAD_SCHEMA_V1 = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#EvolutionCommandCompletedPayload"
BEHAVIOUR_EVOLUTION_AGENT_MESSAGE_PAYLOAD_SCHEMA_V1 = f"{BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1}#EvolutionAgentMessagePayload"

# Daemon extension (authenticated, environment-scoped daemon interaction)
# See: https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0
DAEMON_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0"

# Context extensions
# See: https://docs.aion.to/a2a/extensions/aion/context
GET_CONTEXT_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/context/get-context/1.0.0"
GET_CONTEXTS_LIST_EXTENSION_URI_V1 = "https://docs.aion.to/a2a/extensions/aion/context/get-contexts/1.0.0"
