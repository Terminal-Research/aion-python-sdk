"""Database constants for PostgreSQL schema and table names."""

AION_SCHEMA = "aion"

TASKS_TABLE = "tasks"
TASK_MESSAGES_TABLE = "task_messages"
TASK_ARTIFACTS_TABLE = "task_artifacts"
TASK_VERSIONS_TABLE = "task_versions"
"""a2a-sdk's version table: one compare-and-swap counter per task (``a2a.server.models.TaskVersionModel``)."""
TASK_EVENTS_TABLE = "task_events"
"""a2a-sdk's append-only event journal (``a2a.server.models.TaskEventModel``)."""
CONTEXT_RESERVATIONS_TABLE = "context_reservations"
CONTEXT_BINDINGS_TABLE = "context_bindings"

LANGGRAPH_SCHEMA = "aion_langgraph"
"""Schema of the LangGraph checkpoint tables the SDK's checkpointer keeps on this database."""

ADK_SCHEMA = "aion_adk"
"""Schema of the ADK session tables the SDK's session service keeps on this database."""

ADK_LEGACY_USER_ID = "default-user"
"""The ``user_id`` every ADK session was saved under before sessions carried agent and owner."""
