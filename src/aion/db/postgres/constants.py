"""Database constants for PostgreSQL schema and table names."""

AION_SCHEMA = "aion"

MIGRATION_ADVISORY_LOCK_KEY = 7_382_194_611
"""The advisory lock every runner of the SDK migrations, and the permission check before them, takes turns under."""

ABANDONED_SWEEP_LOCK_CLASS = 1_914_276_053
"""The class (first key) of the advisory lock one server at a time takes to sweep an agent's abandoned tasks.

The second key is derived from the agent id, so each agent of a shared database has its own lock.
"""

TASKS_TABLE = "tasks"
TASK_MESSAGES_TABLE = "task_messages"
TASK_ARTIFACTS_TABLE = "task_artifacts"
TASK_VERSIONS_TABLE = "task_versions"
"""a2a-sdk's version table: one compare-and-swap counter per task (``a2a.server.models.TaskVersionModel``)."""
TASK_EVENTS_TABLE = "task_events"
"""a2a-sdk's append-only event journal (``a2a.server.models.TaskEventModel``)."""
CONTEXT_RESERVATIONS_TABLE = "context_reservations"
CONTEXT_BINDINGS_TABLE = "context_bindings"
ADK_ARTIFACT_VERSIONS_TABLE = "adk_artifact_versions"
"""Every version number the ADK artifact service gave an artifact, one row per number, so no two servers give the same one."""

LANGGRAPH_SCHEMA = "aion_langgraph"
"""Schema of the LangGraph checkpoint tables the SDK's checkpointer keeps on this database."""

ADK_SCHEMA = "aion_adk"
"""Schema of the ADK session tables the SDK's session service keeps on this database."""

ADK_LEGACY_USER_ID = "default-user"
"""The ``user_id`` every ADK session was saved under before sessions carried agent and owner."""
