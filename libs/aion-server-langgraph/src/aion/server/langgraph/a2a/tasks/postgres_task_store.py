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

        async def save_task(self, task: Task) -> None:  # pragma: no cover
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

    async def save_task(self, task: Task) -> None:
        """Persist a ``Task`` to the database."""
        record = TaskRecord(
            id=uuid.uuid4(),
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
