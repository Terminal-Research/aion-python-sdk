"""The ownership port and the implementation for a single-process deployment."""

from __future__ import annotations

import uuid
from typing import Protocol

from .types import Busy, Claim, Lost, Owned, OwnershipLossCallback, Unknown

__all__ = ["OwnershipProvider", "DegenerateOwnershipProvider"]


class OwnershipProvider(Protocol):
    """Internal port implemented by the distributed and single-process providers.

    There is deliberately no ``is_owner()``. Ownership is only ever established
    as the result of a conditional write; any answer given before the write has
    already expired by the time the write happens.
    """

    enforcement_enabled: bool
    """Whether refusals are real. False when the store is process-local."""

    reconciler_enabled: bool
    """Whether this process reclaims leases other processes abandoned."""

    async def acquire(self, task_id: str) -> Claim | Busy:
        """Acquire a claim or report that another owner holds it."""
        ...

    async def renew(self, claim: Claim) -> Owned | Lost | Unknown:
        """Renew a claim and report ownership or uncertainty."""
        ...

    async def release(self, claim: Claim) -> None:
        """Conditionally abandon a claim."""
        ...

    def claim_for(self, task_id: str) -> Claim | None:
        """Return the locally held claim, never a database ownership judgment."""
        ...

    def snapshot(self) -> list[Claim]:
        """Return a copy of the locally held claim map."""
        ...

    def set_loss_callback(self, callback: OwnershipLossCallback | None) -> None:
        """Set the callback used when heartbeat can no longer permit work."""
        ...

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Forget one claim incarnation and notify the execution registry."""
        ...

    def start(self) -> None:
        """Start process-local background supervision."""
        ...

    async def stop(self) -> None:
        """Stop process-local supervision."""
        ...

    async def reconcile(self) -> int:
        """Run one reconciliation pass and return the number of settlements."""
        ...

    async def current_owner(self, task_id: str) -> str | None:
        """Best-effort, non-authoritative read of who currently holds a lease.

        For enriching a ``Busy`` refusal only. Never used to grant or claim
        ownership — И4 still holds: presence here proves nothing about this
        process, and the answer can already be stale by the time it is read.
        """
        ...


class DegenerateOwnershipProvider:
    """No-op ownership provider for an explicitly single-process store.

    It intentionally keeps no claim map. The in-memory ``ActiveTaskRegistry``
    already provides single-process mutual exclusion, which is stricter than a
    lease, and a fake lease map would make the reconciler mistake every live
    task for an orphan.

    It exists so that the single-process build runs the same code path as the
    distributed one: the refusal branches are present in both, even though one
    of them never takes them.
    """

    enforcement_enabled = False
    reconciler_enabled = False

    async def acquire(self, task_id: str) -> Claim:
        """Return a synthetic claim; no external state is touched."""
        return self._synthetic_claim(task_id)

    async def renew(self, claim: Claim) -> Owned:
        """Always report ownership in the single-process implementation."""
        return Owned()

    async def release(self, claim: Claim) -> None:
        """Release is a no-op because no claim map exists."""

    def claim_for(self, task_id: str) -> Claim:
        """Return a synthetic token for the unfenced in-memory store."""
        return self._synthetic_claim(task_id)

    @staticmethod
    def _synthetic_claim(task_id: str) -> Claim:
        """Build a claim that proves nothing and expires never."""
        return Claim(
            task_id=task_id,
            owner_token=uuid.uuid4(),
            lease_expires_at=None,
            deadline=float("inf"),
        )

    def snapshot(self) -> list[Claim]:
        """Return no claims: the provider deliberately has no map."""
        return []

    def set_loss_callback(self, callback: OwnershipLossCallback | None) -> None:
        """Accept the common callback wiring; nothing here can ever lose a lease."""

    def mark_lost(self, claim: Claim, reason: str) -> None:
        """Ignore loss notifications because this provider has no claim map."""

    def start(self) -> None:
        """There is no background work in the single-process provider."""

    async def stop(self) -> None:
        """There is no background work to stop."""

    async def reconcile(self) -> int:
        """Never reconcile in-memory tasks as distributed orphans."""
        return 0

    async def current_owner(self, task_id: str) -> None:
        """Report no owner: this provider has no distributed state to read."""
        return None
