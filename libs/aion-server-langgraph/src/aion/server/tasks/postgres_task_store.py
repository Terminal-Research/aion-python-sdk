"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from aion.server.db.models import TaskRecord

try:  # pragma: no cover - optional dependency
    from a2a.server.tasks import TaskStore
    from a2a.types import Task
except Exception:  # pragma: no cover - tests without a2a
    class Task(BaseModel):
        id: str
        contextId: str


    class TaskStore:  # type: ignore
        """Fallback ``TaskStore`` protocol."""

        async def save(self, task: Task) -> None:  # pragma: no cover
            raise NotImplementedError

        async def get(self, task_id: str) -> Task | None:  # pragma: no cover
            raise NotImplementedError

        async def delete(self, task_id: str) -> None:  # pragma: no cover
            raise NotImplementedError


class PostgresTaskStore(TaskStore):
    """Store tasks in a Postgres database using connection pool."""

    def __init__(self, pool: ConnectionPool) -> None:
        if pool is None:
            raise ValueError("Connection pool is required")
        self._pool = pool

    async def save(self, task: Task) -> None:
        """Persist a ``Task`` to the database."""
        try:
            task_id = str(uuid.UUID(task.id))
        except Exception:
            task_id = str(uuid.uuid4())

        record = TaskRecord(
            id=uuid.UUID(task_id),
            context_id=task.contextId,
            task=task,
            created_at=_dt.datetime.utcnow(),
            updated_at=_dt.datetime.utcnow(),
        )

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tasks (id, context_id, task, created_at, updated_at)
                    VALUES (%s, %s, %s::jsonb, %s, %s) ON CONFLICT (id) DO
                    UPDATE
                        SET task = EXCLUDED.task,
                        context_id = EXCLUDED.context_id,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        str(record.id),
                        record.context_id,
                        record.task.model_dump_json(),
                        record.created_at,
                        record.updated_at,
                    ),
                )

    async def get(self, task_id: str) -> Task | None:
        """Retrieve a task by ID."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task FROM tasks WHERE id = %s",
                    (str(task_id),),
                )
                row = cur.fetchone()

        if not row:
            return None

        data: Any = row[0]
        if isinstance(data, str):
            import json
            data = json.loads(data)

        return Task.model_validate(data)

    async def delete(self, task_id: str) -> None:
        """Delete a task by ID."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tasks WHERE id = %s",
                    (str(task_id),),
                )

    # Backwards compatibility
    async def save_task(self, task: Task) -> None:  # pragma: no cover - legacy
        await self.save(task)
