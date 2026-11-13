"""A graph's checkpoints in PostgreSQL hold a file the graph produced as a link, never its bytes.

Every row of the checkpoint tables is read back after a run whose node
answered with a file inline: neither the file's content nor its base64 is
there, in the checkpoints, their blobs or the pending writes.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.content import create_file_block
from langgraph.graph import END, START, MessagesState, StateGraph
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from aion.db.postgres.constants import AION_SCHEMA
from aion.langgraph.server.checkpoint.backends.postgres import AionAsyncPostgresSaver
from tests.unit.support.files import NumberingBackend, storing_turn

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

PDF = b"%PDF-1.7\n" + b"report " * 8
PDF_B64 = base64.b64encode(PDF).decode()
CONFIG = {"configurable": {"thread_id": "thread-1"}}


async def _rows(pool: AsyncConnectionPool, schema: str) -> list[tuple]:
    """Every row of every table in ``schema``."""
    rows: list[tuple] = []
    async with pool.connection() as conn:
        tables = await (
            await conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,))
        ).fetchall()
        for (table,) in tables:
            cursor = await conn.execute(sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
            rows.extend(await cursor.fetchall())
    return rows


def _holds_the_file(value) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        return PDF in value or PDF_B64.encode() in value
    return PDF_B64 in str(value)


async def test_a_file_a_node_returns_is_saved_as_a_link_in_every_table() -> None:
    schema = f"langgraph_files_{uuid.uuid4().hex[:12]}"
    async with AsyncConnectionPool(POSTGRES_TEST_URL, open=False) as pool:
        try:
            saver = AionAsyncPostgresSaver(conn=pool, schema=schema, restore_schema=AION_SCHEMA)
            await saver.setup()
            report = create_file_block(base64=PDF_B64, mime_type="application/pdf", filename="report.pdf")
            builder = StateGraph(MessagesState)
            builder.add_node("answer", lambda state: {"messages": [AIMessage(content=[report])]})
            builder.add_edge(START, "answer")
            builder.add_edge("answer", END)
            graph = builder.compile(checkpointer=saver)

            with storing_turn(NumberingBackend()):
                await graph.ainvoke({"messages": [HumanMessage("Make a report")]}, CONFIG)

            saved = (await graph.aget_state(CONFIG)).values["messages"][-1].content[0]
            assert saved["url"] == "https://files.test/1?grant=g" and "base64" not in saved
            rows = await _rows(pool, schema)
            assert rows
            assert not any(_holds_the_file(value) for row in rows for value in row)
        finally:
            async with pool.connection() as conn:
                await conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
