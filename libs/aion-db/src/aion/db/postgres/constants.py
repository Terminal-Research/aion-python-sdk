"""Database constants for PostgreSQL schema and table names."""

AION_SCHEMA = "aion"

TASKS_TABLE = "tasks"
TASK_CLAIMS_TABLE = "task_claims"
TASK_MESSAGES_TABLE = "task_messages"
TASK_ARTIFACTS_TABLE = "task_artifacts"

TASK_EVENT_CHANNEL = "task_events"
"""``LISTEN``/``NOTIFY`` channel carrying every cross-pod task event.

One channel for every task and every event kind, not one channel per task or
per kind: ``LISTEN`` takes no bind parameter, so a channel-per-task scheme
would need to build channel names as strings, and a channel-per-kind scheme
would need every pod to already know every kind at startup. A shared channel
with a typed JSON payload (see ``aion.db.postgres.events.TaskEvent``) avoids
both and keeps subscription static for the life of the process. Notification
volume stays low: every publisher only emits conditionally, on a genuine
state change a pod is actually waiting on - see
``TaskClaimsRepository.request_cancel`` and
``TaskClaimsRepository.notify_cancel_resolved``."""
