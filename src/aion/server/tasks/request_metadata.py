"""Inbound extension payloads in stored tasks: finding them and removing them.

A task's metadata holds what the server and trusted handlers write - the
routed extension, a settlement reason, an extension handler's own state. The
payloads of inbound extensions arrive in a request's ``params.metadata`` and
belong to that request: the agent reads them from the request, and the daemon
payload among them carries the environment's configuration variables in plain
text. A task row, or a ``Task`` snapshot in a2a-sdk's ``task_events`` journal,
that holds such a payload hands it to every reader of the task - GetTask,
ListTasks, stream snapshots, push notifications - for as long as the row
lives, and nothing prunes the journal.

:func:`strip_request_metadata` counts those keys and, when asked to, removes
them from both tables. It is what ``aion db strip-request-metadata`` runs.
Every other key stays where it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from a2a.types import StreamResponse
from sqlalchemy import text

from aion.core.db import DbManagerProtocol
from aion.core.runtime.context.extensions import TaskMetadataCollector, aion_a2a_extension_registry
from aion.db.postgres.constants import TASK_EVENTS_TABLE, TASKS_TABLE

__all__ = [
    "RequestMetadataFinding",
    "request_metadata_keys",
    "strip_request_metadata",
]

_EVENT_BATCH_SIZE = 500
"""Journal rows decoded per transaction."""


@dataclass(frozen=True)
class RequestMetadataFinding:
    """What one table holds of the request metadata keys.

    Attributes:
        table: The table that was searched.
        rows: Rows that hold at least one of the keys.
        keys: Keys found across those rows.
    """

    table: str
    rows: int
    keys: int


def request_metadata_keys() -> tuple[str, ...]:
    """The metadata keys that only a request carries.

    One per registered extension whose payload a request carries in
    ``params.metadata`` - those read by ``TaskMetadataCollector``: daemon,
    distribution, cron and traceability among the SDK's own. The server never
    writes one of these keys into a task, so any of them found in a stored
    task came from a request.

    Returns:
        The extension URIs, in registration order.
    """
    return tuple(
        descriptor.uri
        for descriptor in aion_a2a_extension_registry.get_all()
        if isinstance(descriptor.collector, TaskMetadataCollector)
    )


async def strip_request_metadata(
        db_manager: DbManagerProtocol,
        *,
        apply: bool,
) -> list[RequestMetadataFinding]:
    """Count the keys of :func:`request_metadata_keys` in stored tasks, and remove them when ``apply``.

    The ``tasks`` rows are changed in one statement; a row left with no
    metadata at all gets ``NULL``, which reads as a task without metadata.
    The journal is read in batches, each in its own transaction, and only the
    rows whose bytes contain one of the keys are decoded: a ``Task`` snapshot
    holding one is written back without it, and every other event is left as
    it was. It may run while servers are serving: it changes neither a
    task's version nor any other key, and a second run removes whatever a
    task still being written put back.

    Args:
        db_manager: An initialized database manager.
        apply: Remove the keys; without it nothing is changed.

    Returns:
        One finding for ``tasks`` and one for ``task_events``. With ``apply``
        they count what was removed.
    """
    keys = request_metadata_keys()
    if not keys:
        return [RequestMetadataFinding(TASKS_TABLE, 0, 0), RequestMetadataFinding(TASK_EVENTS_TABLE, 0, 0)]
    return [
        await _strip_tasks(db_manager, keys, apply=apply),
        await _strip_journal(db_manager, keys, apply=apply),
    ]


async def _strip_tasks(
        db_manager: DbManagerProtocol, keys: tuple[str, ...], *, apply: bool
) -> RequestMetadataFinding:
    """Count, then optionally remove, the keys in the ``metadata`` column of ``tasks``."""
    holding = "jsonb_typeof(metadata) = 'object' AND metadata ?| CAST(:keys AS text[])"
    count = text(
        f"SELECT count(*), coalesce(sum(found), 0) FROM ("
        f" SELECT (SELECT count(*) FROM jsonb_object_keys(metadata) AS key"
        f" WHERE key = ANY(CAST(:keys AS text[]))) AS found"
        f" FROM {TASKS_TABLE} WHERE {holding}"
        f") AS holding_rows"
    )
    remove = text(
        f"UPDATE {TASKS_TABLE} SET metadata = CASE"
        f" WHEN metadata - CAST(:keys AS text[]) = '{{}}'::jsonb THEN NULL"
        f" ELSE metadata - CAST(:keys AS text[]) END"
        f" WHERE {holding}"
    )
    async with db_manager.get_session() as session:
        async with session.begin():
            rows, found = (await session.execute(count, {"keys": list(keys)})).one()
            if apply and rows:
                await session.execute(remove, {"keys": list(keys)})
    return RequestMetadataFinding(TASKS_TABLE, int(rows), int(found))


async def _strip_journal(
        db_manager: DbManagerProtocol, keys: tuple[str, ...], *, apply: bool
) -> RequestMetadataFinding:
    """Count, then optionally remove, the keys in the ``Task`` snapshots of ``task_events``.

    ``event_data`` is a serialized ``StreamResponse``. A protobuf string - a
    ``Struct`` key among them - is stored as its UTF-8 bytes, so a row whose
    bytes contain none of the keys cannot hold one and is never decoded.
    """
    patterns = {f"key_{index}": key.encode("utf-8") for index, key in enumerate(keys)}
    contains = " OR ".join(f"position(:{name} IN event_data) > 0" for name in patterns)
    candidates = text(
        f"SELECT seq, event_data FROM {TASK_EVENTS_TABLE}"
        f" WHERE seq > :after AND ({contains}) ORDER BY seq LIMIT :limit"
    )
    rewrite = text(f"UPDATE {TASK_EVENTS_TABLE} SET event_data = :event_data WHERE seq = :seq")

    rows = found = 0
    after = 0
    while True:
        async with db_manager.get_session() as session:
            async with session.begin():
                batch = (
                    await session.execute(
                        candidates, {"after": after, "limit": _EVENT_BATCH_SIZE, **patterns}
                    )
                ).all()
                for seq, event_data in batch:
                    event = StreamResponse()
                    event.ParseFromString(bytes(event_data))
                    if not event.HasField("task"):
                        continue
                    present = [key for key in keys if key in event.task.metadata]
                    if not present:
                        continue
                    rows += 1
                    found += len(present)
                    if not apply:
                        continue
                    for key in present:
                        del event.task.metadata[key]
                    if not event.task.metadata:
                        event.task.ClearField("metadata")
                    await session.execute(
                        rewrite, {"event_data": event.SerializeToString(), "seq": seq}
                    )
        if len(batch) < _EVENT_BATCH_SIZE:
            return RequestMetadataFinding(TASK_EVENTS_TABLE, rows, found)
        after = batch[-1][0]
