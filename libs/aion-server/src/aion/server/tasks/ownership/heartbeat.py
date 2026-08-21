"""The single supervisor that keeps every lease this process holds alive."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from .types import Claim, Unknown

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
        """Renew every held claim in as few round trips as retries allow.

        One batched statement replaces one transaction per claim. A batch
        that executes is definitive for each claim in it - exactly as
        calling ``renew`` once per claim would report, just paid for once -
        so retrying is only ever needed when the statement itself failed or
        timed out, uncertain for every claim still pending at once rather
        than one ``Unknown`` per claim.
        """
        pending = list(claims)
        try:
            while pending:
                outcome = await self.provider.renew_batch(pending)
                if not isinstance(outcome, Unknown):
                    # Every claim in this attempt now has a definitive
                    # Owned or Lost outcome, already applied by the provider.
                    return
                pending = self._survivors(pending)
                if not pending:
                    return
                await self._backoff(pending)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A bug here must not silently leave a claim renewing forever:
            # fail closed on whatever is still pending rather than let the
            # exception escape and kill the supervisor loop.
            for claim in pending:
                self.provider.mark_lost(claim, "renew_supervisor_error")
            logger.error(
                "Unexpected task ownership heartbeat failure for %s",
                [claim.task_id for claim in pending],
                exc_info=exc,
            )

    def _survivors(self, pending: list[Claim]) -> list[Claim]:
        """Drop claims whose fail-closed deadline has already passed.

        An unknown outcome is the absence of a fact, not a loss: treating it
        as one would drop live tasks over a blink of the network. Treating it
        as permission to continue would be worse. So each claim keeps
        retrying only until the deadline of its last confirmed renewal.
        """
        survivors = []
        for claim in pending:
            if time.monotonic() >= claim.deadline:
                self.provider.mark_lost(claim, "renew_deadline")
            else:
                survivors.append(claim)
        return survivors

    async def _backoff(self, pending: list[Claim]) -> None:
        """Wait before the next retry, bounded by the nearest deadline."""
        retry_seconds = self.provider.settings.unknown_retry_seconds
        retry = random.uniform(retry_seconds * 0.75, retry_seconds * 1.25)
        remaining = min(claim.deadline for claim in pending) - time.monotonic()
        await asyncio.sleep(max(0.0, min(retry, remaining)))
