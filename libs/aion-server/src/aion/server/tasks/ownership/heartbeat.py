"""The single supervisor that keeps every lease this process holds alive."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from .types import Claim, Lost, Owned

__all__ = ["OwnershipHeartbeat"]

logger = logging.getLogger(__name__)


class OwnershipHeartbeat:
    """Renews all locally held claims from one loop, in non-overlapping ticks.

    One supervisor rather than one task per lease: the work list is the
    provider's claim map, and there is no second source of truth about what
    this process is executing.

    It lives in the event loop that runs the executions. Not in a thread: a
    blocked loop that keeps renewing its leases through a separate thread is a
    runtime unable to process a cancellation while still holding the right to
    execute.
    """

    def __init__(self, provider) -> None:
        """Bind heartbeat timing to one provider."""
        self.provider = provider

    async def run(self) -> None:
        """Renew claims in non-overlapping ticks measured from tick start."""
        while True:
            started = time.monotonic()
            claims = self.provider.snapshot()
            if claims:
                await self._renew_all(claims)
            # Measured from the start of the tick: timing it from the end lets
            # a slow database stretch the interval until the TTL is eaten by
            # drift.
            delay = max(
                0.0,
                started + self.provider.settings.heartbeat_interval_seconds - time.monotonic(),
            )
            if delay:
                await asyncio.sleep(delay)

    async def _renew_all(self, claims: list[Claim]) -> None:
        """Renew every claim concurrently and fail closed on a surprise error."""
        results = await asyncio.gather(
            *(self._renew_until_known(claim) for claim in claims),
            return_exceptions=True,
        )
        for claim, result in zip(claims, results, strict=True):
            if isinstance(result, Exception):
                self.provider.mark_lost(claim, "renew_supervisor_error")
                logger.error(
                    "Unexpected task ownership heartbeat failure for %s",
                    claim.task_id,
                    exc_info=result,
                )

    async def _renew_until_known(self, claim: Claim) -> None:
        """Retry unknown renewals until the fail-closed deadline.

        An unknown outcome is the absence of a fact, not a loss: treating it as
        one would drop live tasks over a blink of the network. Treating it as
        permission to continue would be worse. So work is allowed to continue
        until the deadline of the last confirmed renewal, and then stops
        without writing anything.
        """
        while True:
            if time.monotonic() >= claim.deadline:
                self.provider.mark_lost(claim, "renew_deadline")
                return

            result = await self.provider.renew(claim)
            if isinstance(result, Owned):
                # A confirmed renewal is proof, and the provider has already
                # recorded the deadline it starts. Discarding the claim because
                # the previous deadline passed while this call was in flight
                # would tear down an execution that demonstrably still owns its
                # lease.
                return
            if isinstance(result, Lost):
                self.provider.mark_lost(claim, "renew_lost")
                return

            remaining = claim.deadline - time.monotonic()
            if remaining <= 0:
                self.provider.mark_lost(claim, "renew_unknown_deadline")
                return

            retry_seconds = self.provider.settings.unknown_retry_seconds
            retry = random.uniform(retry_seconds * 0.75, retry_seconds * 1.25)
            await asyncio.sleep(min(retry, remaining))
