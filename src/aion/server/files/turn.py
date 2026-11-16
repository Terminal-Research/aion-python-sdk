"""The files of one turn, each stored once, for the task and for the framework's state.

An agent's file can reach storage on two paths in one turn. The event
pipeline stores the files the agent sends out, so that the task holds their
links. A framework adapter stores the files its framework is about to keep in
its own state - a LangGraph checkpoint, an ADK session - so that the state
holds the link too and the database never holds the bytes. ``TurnFiles``
serves both paths of a turn: whichever asks first stores the file, and the
other gets the same link (``TurnUploads``), whatever order the framework
produces them in. The task and the framework's state then point at one
stored file.

The server opens one for every turn (``turn_files``); a framework adapter
reaches it with ``current_turn_files``. With no storage backend configured
there is none: the framework's state keeps the bytes, as the task does.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, TypeVar

from a2a.server.events import Event
from a2a.types import Message, Part, Role

from aion.core.a2a.models import A2AOutbox
from aion.core.exceptions import AionError
from aion.core.runtime.context import get_aion_runtime_context
from aion.server.files.a2a import A2AFileTransformer, TurnUploads
from aion.server.files.storage import UploadFailure, resolve_upload_context

__all__ = [
    "StateFileNotStoredError",
    "TurnFiles",
    "current_turn_files",
    "turn_files",
]

E = TypeVar("E", bound=Event)


class StateFileNotStoredError(AionError):
    """A file the framework was about to keep in its state could not be stored, so the task fails.

    The state is not written with the bytes instead: with storage configured,
    no file is kept in the database. Each file's own reason is in the log
    line the transformer wrote for it; the client reads ``public_reason``.
    """

    def __init__(self, failures: list[UploadFailure]) -> None:
        """Initialize the error.

        Args:
            failures: Every failure of the files asked for; never empty.
        """
        self.failures = failures
        codes = ", ".join(sorted({failure.error_code.value for failure in failures}))
        super().__init__(f"{len(failures)} file(s) of the framework's state could not be stored: {codes}")

    @property
    def public_reason(self) -> str:
        """The safe, stable sentence the client reads, from the first failure."""
        return self.failures[0].public_reason


class TurnFiles:
    """Stores the files of one turn's task, each once.

    Args:
        transformer: The server's transformer, with a storage backend.
        context_id: The context of the turn's task.
        task_id: The turn's task.
    """

    def __init__(self, transformer: A2AFileTransformer, *, context_id: str, task_id: str) -> None:
        self._transformer = transformer
        self._context_id = context_id
        self._task_id = task_id
        self.uploads = TurnUploads()
        """What the turn has stored; the event pipeline stores through it too."""

    async def keep(self, event: E) -> E:
        """The event as a framework keeps it in its state: its inline files stored, its parts linking to them.

        The files are the agent's, so each keeps the retention its part asks
        for, as when the agent sends it out. An event with no inline file is
        returned as it is.

        Raises:
            StateFileNotStoredError: A file could not be stored.
        """
        transform = await self._transformer.transform(
            event,
            upload_context=resolve_upload_context(
                get_aion_runtime_context(), context_id=self._context_id, task_id=self._task_id
            ),
            turn=self.uploads,
        )
        if transform.report.failures:
            raise StateFileNotStoredError(transform.report.failures)
        return transform.event

    async def keep_parts(self, parts: Sequence[Part]) -> list[Part]:
        """``keep`` for parts: each inline file stored and replaced by its link, the rest as they are.

        Raises:
            StateFileNotStoredError: A file could not be stored.
        """
        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=list(parts),
            context_id=self._context_id,
            task_id=self._task_id,
        )
        return list((await self.keep(message)).parts)

    async def keep_outbox(self, outbox: A2AOutbox) -> A2AOutbox:
        """``keep`` for an outbox: the files of its task or message stored and linked.

        Raises:
            StateFileNotStoredError: A file could not be stored.
        """
        task = await self.keep(outbox.task) if outbox.task is not None else None
        message = await self.keep(outbox.message) if outbox.message is not None else None
        if task is outbox.task and message is outbox.message:
            return outbox
        return A2AOutbox(task=task, message=message)


_current: ContextVar[Optional[TurnFiles]] = ContextVar("aion_turn_files", default=None)


def current_turn_files() -> Optional[TurnFiles]:
    """The files of the turn running in this context; None outside a turn or without storage."""
    return _current.get()


@contextmanager
def turn_files(files: Optional[TurnFiles]) -> Iterator[None]:
    """Make ``files`` the turn's for the code run inside, and the tasks it creates."""
    token = _current.set(files)
    try:
        yield
    finally:
        _current.reset(token)
