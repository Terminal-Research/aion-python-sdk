"""The vocabulary of task ownership: a claim, the answers about it, its errors.

Acquiring, renewing and releasing a lease each have more than two outcomes, and
the difference between them drives different behaviour: a refused acquire is
ordinary, a lost renewal tears an execution down, and an unknown one starts a
countdown. They are separate types so that a caller cannot collapse them into a
boolean and lose the distinction.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from a2a.utils.errors import A2AError

from aion.server.core.errors import register_aion_error

__all__ = [
    "Claim",
    "Busy",
    "Owned",
    "Lost",
    "Unknown",
    "OwnershipLossCallback",
    "ControlSignal",
    "ControlSignalCallback",
    "TaskOwnershipBusy",
    "TaskOwnershipLost",
]


class TaskOwnershipLost(RuntimeError):
    """Raised when a fenced task write no longer proves ownership."""

    def __init__(self, task_id: str) -> None:
        """Create an ownership-loss error without exposing the owner token."""
        self.task_id = task_id
        super().__init__(f"Task ownership was lost for {task_id}")


class TaskOwnershipBusy(A2AError):
    """An A2A error for a task owned by another process, right now.

    Deliberately not an ``InvalidParamsError``: the request is well formed and
    the client has nothing to correct — the condition is retryable on its own,
    which is why it gets its own JSON-RPC code (``TASK_OWNERSHIP_BUSY_CODE``,
    registered into a2a-sdk's error maps below) rather than reusing the
    generic -32603 an unmapped ``A2AError`` would otherwise fall back to. A
    client can treat the code itself as the "retry me" signal instead of
    parsing a ``data`` flag for it.
    """

    def __init__(self, task_id: str, owner_instance_id: str | None = None) -> None:
        """Create the public busy response.

        Args:
            task_id: Identifier of the task that could not be processed.
            owner_instance_id: Best-effort identity of the instance currently
                holding the lease, when a diagnostic read could find one. Never
                used by this process to make a decision — only surfaced so that
                whatever sits in front of this server (routing, an operator, a
                dashboard) can act on it if it chooses to. Its own correctness
                does not depend on this value: it may already be stale by the
                time it is read.
        """
        data = {"task_id": task_id}
        if owner_instance_id is not None:
            data["owner_instance_id"] = owner_instance_id
        super().__init__(
            message="Task is owned by another server",
            data=data,
        )
        self.task_id = task_id
        self.owner_instance_id = owner_instance_id


# Registered right beside the class it describes, so the patch cannot drift
# out of sync with it. See aion.server.core.errors for why this patches
# a2a-sdk's own maps instead of using some aion-owned extension point (there
# isn't one) and why -32050 - the bottom of Aion's reserved block - rather
# than the next free slot after a2a-sdk's own codes.
TASK_OWNERSHIP_BUSY_CODE = -32050

register_aion_error(
    TaskOwnershipBusy,
    TASK_OWNERSHIP_BUSY_CODE,
    http_status=409,
    grpc_status="ABORTED",
    reason="TASK_OWNERSHIP_BUSY",
)


@dataclass(frozen=True, slots=True)
class Claim:
    """A locally held task claim and its fail-closed monotonic deadline.

    ``lease_expires_at`` is the database value, kept for diagnostics. The
    heartbeat uses ``deadline`` instead: it is measured from the beginning of
    the last successful database call, on a local monotonic clock, and
    therefore cannot extend local work on the strength of a database timestamp
    this process has no way to verify.
    """

    task_id: str
    owner_token: uuid.UUID
    lease_expires_at: datetime | None
    deadline: float


@dataclass(frozen=True, slots=True)
class Busy:
    """The database refused an acquire because a live owner exists."""


@dataclass(frozen=True, slots=True)
class Owned:
    """A renewal returned the claim row for the presented token."""

    lease_expires_at: datetime | None = None
    cancel_requested: bool = False
    """Whether a non-owner asked this claim's owner to cancel the task.

    Carried on the same round trip that renews the lease, from the same
    row - a non-owner cannot run this task's teardown itself, so it can only
    mark the claim and wait for the owner to notice here and act locally.
    """


@dataclass(frozen=True, slots=True)
class Lost:
    """A renewal affected no row: the token is no longer the owner."""


@dataclass(frozen=True, slots=True)
class Unknown:
    """A renewal could not establish either ownership outcome."""

    error: Exception | None = None


OwnershipLossCallback = Callable[[str, str], None]
"""Called with ``(task_id, reason)`` once work on a task may no longer continue."""


class ControlSignal(enum.Enum):
    """A request from a non-owner to this claim's owner, carried on the lease.

    One member today. Named and typed as a set rather than a lone boolean so
    a second signal - something other than cancellation - can be added later
    without renaming the callback or its plumbing between the ownership
    provider and the execution registry.
    """

    CANCEL = "cancel"


ControlSignalCallback = Callable[[str, ControlSignal], None]
"""Called with ``(task_id, signal)`` once a held claim carries a new request."""
