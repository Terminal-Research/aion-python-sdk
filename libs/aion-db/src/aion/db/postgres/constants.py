"""Database constants for PostgreSQL schema and table names."""

AION_SCHEMA = "aion"

TASKS_TABLE = "tasks"
TASK_CLAIMS_TABLE = "task_claims"
TASK_MESSAGES_TABLE = "task_messages"
TASK_ARTIFACTS_TABLE = "task_artifacts"

TASK_TERMINAL_CHANNEL = "task_terminal"
"""``LISTEN``/``NOTIFY`` channel used to wake a pod waiting on a cancellation
it signaled through ``task_claims.cancel_requested_at`` but does not own.
One channel for every task, not one channel per task: ``LISTEN`` takes no
bind parameter, so a channel-per-task scheme would need to build channel
names as strings; a shared channel with the task id as payload avoids that
and keeps subscription static for the life of the process. Notification
volume stays low because it is only ever emitted conditionally, when a
cancellation was actually requested - see
``TaskClaimsRepository.notify_terminal_if_cancel_requested``."""
