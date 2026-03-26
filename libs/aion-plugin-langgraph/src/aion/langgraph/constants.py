"""Constants for the LangGraph plugin."""

AION_LANGGRAPH_SCHEMA = "aion_langgraph"

# State field names that are re-injected on every invocation and must never be checkpointed.
AION_UNTRACKED_CHANNEL_NAMES = frozenset({"a2a_inbox"})
