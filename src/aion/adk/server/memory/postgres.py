"""ADK's memory on PostgreSQL: the text of what an agent put in memory, searched by its words."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Optional

from google.adk.events import Event
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.genai import types
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aion.adk.server.constants import AION_ADK_SCHEMA
from aion.adk.server.tables import MEMORY_TABLE

__all__ = ["MAX_SEARCH_RESULTS", "AionADKMemoryService", "text_only"]

MAX_SEARCH_RESULTS = 10
"""How many memories a search answers with at most, the best matches first."""

MAX_QUERY_WORDS = 32
"""How many distinct words of a query a search matches on."""

_WORD = re.compile(r"\w+")


def text_only(content: Optional[types.Content]) -> Optional[types.Content]:
    """``content`` with its text parts alone, or None when it has none.

    A file's bytes, a function call or response and the model's thoughts are
    left out: memory keeps what was said.
    """
    if content is None or not content.parts:
        return None
    parts = [types.Part(text=part.text) for part in content.parts if part.text and not part.thought]
    if not parts:
        return None
    return types.Content(role=content.role, parts=parts)


class AionADKMemoryService(BaseMemoryService):
    """A memory service on the server's database, keyed by agent and owner as the sessions are.

    ADK's ``app_name`` and ``user_id`` are the Aion agent id and the
    conversation's state owner, so an agent finds only what it put in memory
    for that owner, in any of the owner's conversations. Nothing is written
    unless the agent writes it - ``add_session_to_memory`` from a callback,
    as ADK has it - and adding a session again adds only its new events.
    Only text is kept (``text_only``); a search matches the words of the
    query, in any language, and answers with the best ``MAX_SEARCH_RESULTS``.
    """

    def __init__(self, engine: AsyncEngine, schema: str = AION_ADK_SCHEMA) -> None:
        self._engine = engine
        self._table = '"' + schema.replace('"', '""') + f'".{MEMORY_TABLE}'

    async def add_session_to_memory(self, session: Session) -> None:
        """Keep the text of every event of ``session`` not kept yet."""
        await self._insert(_event_rows(session.app_name, session.user_id, session.id, session.events, None))

    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: Sequence[Event],
        session_id: Optional[str] = None,
        custom_metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Keep the text of ``events`` not kept yet."""
        await self._insert(_event_rows(app_name, user_id, session_id or "", events, custom_metadata))

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Keep the text of entries the agent wrote itself."""
        rows = []
        for memory in memories:
            content = text_only(memory.content)
            if content is None:
                continue
            metadata = {**(custom_metadata or {}), **(memory.custom_metadata or {})}
            rows.append(
                _row(app_name, user_id, "", memory.id or uuid.uuid4().hex, memory.author, memory.timestamp, content, metadata)
            )
        await self._insert(rows)

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        """The memories of this agent and owner that share the most words with ``query``."""
        words = list(dict.fromkeys(word.lower() for word in _WORD.findall(query)))[:MAX_QUERY_WORDS]
        if not words:
            return SearchMemoryResponse(memories=[])
        params: dict[str, Any] = {f"w{index}": word for index, word in enumerate(words)}
        terms = " || ".join(f"plainto_tsquery('simple', :w{index})" for index in range(len(words)))
        params.update(app_name=app_name, user_id=user_id, limit=MAX_SEARCH_RESULTS)
        statement = text(
            f"SELECT entry_id, author, \"timestamp\", content, custom_metadata FROM {self._table} "
            f"CROSS JOIN (SELECT {terms} AS query) AS q "
            "WHERE app_name = :app_name AND user_id = :user_id AND search @@ q.query "
            "ORDER BY ts_rank(search, q.query) DESC, created_at DESC LIMIT :limit"
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(statement, params)).mappings().all()
        return SearchMemoryResponse(
            memories=[
                MemoryEntry(
                    id=row["entry_id"],
                    author=row["author"],
                    timestamp=row["timestamp"],
                    content=types.Content.model_validate(row["content"]),
                    custom_metadata=row["custom_metadata"] or {},
                )
                for row in rows
            ]
        )

    async def _insert(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        statement = text(
            f"INSERT INTO {self._table} "
            '(app_name, user_id, session_id, entry_id, author, "timestamp", content, custom_metadata, text) '
            "VALUES (:app_name, :user_id, :session_id, :entry_id, :author, :timestamp, "
            "CAST(:content AS jsonb), CAST(:custom_metadata AS jsonb), :text) ON CONFLICT DO NOTHING"
        )
        async with self._engine.begin() as conn:
            await conn.execute(statement, rows)


def _event_rows(
    app_name: str,
    user_id: str,
    session_id: str,
    events: Iterable[Event],
    custom_metadata: Optional[Mapping[str, object]],
) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        content = text_only(event.content)
        if content is None:
            continue
        timestamp = datetime.fromtimestamp(event.timestamp).isoformat() if event.timestamp else None
        rows.append(_row(app_name, user_id, session_id, event.id, event.author, timestamp, content, custom_metadata))
    return rows


def _row(
    app_name: str,
    user_id: str,
    session_id: str,
    entry_id: str,
    author: Optional[str],
    timestamp: Optional[str],
    content: types.Content,
    custom_metadata: Optional[Mapping[str, object]],
) -> dict[str, Any]:
    return {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "entry_id": entry_id,
        "author": author,
        "timestamp": timestamp,
        "content": content.model_dump_json(exclude_none=True),
        "custom_metadata": json.dumps(dict(custom_metadata or {}), default=str),
        "text": " ".join(part.text for part in content.parts or () if part.text),
    }
