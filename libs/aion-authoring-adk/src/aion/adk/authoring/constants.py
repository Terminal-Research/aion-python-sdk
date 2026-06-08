"""ADK authoring constants."""

AION_OUTPUT_KEY = "aion:output"
"""Metadata key used in ADK Event.custom_metadata to route events to specific A2A artifact types."""

AION_ROUTING_KEY = "aion:routing"
"""Metadata key used in ADK Event.custom_metadata to carry outbound delivery routing (MessageActionPayload)."""

AION_SERVICE_KEYS: frozenset[str] = frozenset({AION_OUTPUT_KEY, AION_ROUTING_KEY})
"""Set of all reserved aion: service keys in Event.custom_metadata.
Everything outside this set is treated as user metadata and forwarded to A2A Message.metadata."""
