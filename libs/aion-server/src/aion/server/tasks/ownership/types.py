"""The vocabulary of task ownership: a claim, the answers about it, its errors.

Acquiring, renewing and releasing a lease each have more than two outcomes, and
the difference between them drives different behaviour: a refused acquire is
ordinary, a lost renewal tears an execution down, and an unknown one starts a
countdown. They are separate types so that a caller cannot collapse them into a
boolean and lose the distinction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from a2a.utils.errors import A2AError

__all__ = [
    "Claim",
    "Busy",
    "Owned",
    "Lost",
    "Unknown",
    "OwnershipLossCallback",
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
    """A retryable A2A error for a task owned by another process.

    Deliberately not an ``InvalidParamsError``: the request is well formed and
    the client has nothing to correct. Being absent from
    ``JSON_RPC_ERROR_CODE_MAP`` it is reported as -32603 with HTTP 500, the
    server-side class of failure a client may retry, rather than -32602, which
    tells the caller its own parameters were wrong.
    """

    def __init__(self, task_id: str, owner_instance_id: str | None = None) -> None:
        """Create the public busy response and mark it retryable.

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
        data = {"retryable": True, "task_id": task_id}
        if owner_instance_id is not None:
            data["owner_instance_id"] = owner_instance_id
        super().__init__(
            message="I cannot process this task because it is owned by another server; retry later.",
            data=data,
        )
        self.task_id = task_id
        self.owner_instance_id = owner_instance_id


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


@dataclass(frozen=True, slots=True)
class Lost:
    """A renewal affected no row: the token is no longer the owner."""


@dataclass(frozen=True, slots=True)
class Unknown:
    """A renewal could not establish either ownership outcome."""

    error: Exception | None = None


OwnershipLossCallback = Callable[[str, str], None]
"""Called with ``(task_id, reason)`` once work on a task may no longer continue."""
