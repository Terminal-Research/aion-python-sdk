"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import psycopg
from pydantic import BaseModel

from aion.server.db import get_config
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
    """Store tasks in a Postgres database."""

    def __init__(self, dsn: str | None = None) -> None:
        cfg = get_config()
        self._dsn = dsn or (cfg.url if cfg else None)

    def _connect(self) -> psycopg.Connection:
        if not self._dsn:
            raise RuntimeError("Postgres DSN is not configured")
        return psycopg.connect(self._dsn)

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
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tasks (id, context_id, task, created_at, updated_at)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO UPDATE
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
                conn.commit()

    async def get(self, task_id: str) -> Task | None:
        """Retrieve a task by ID."""
        with self._connect() as conn:
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
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tasks WHERE id = %s",
                    (str(task_id),),
                )
                conn.commit()

    # Backwards compatibility
    async def save_task(self, task: Task) -> None:  # pragma: no cover - legacy
        await self.save(task)
