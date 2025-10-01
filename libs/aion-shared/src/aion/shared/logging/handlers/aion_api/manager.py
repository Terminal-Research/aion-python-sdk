import asyncio
import sys
from typing import Optional, List

from aion.shared.logging.base import AionLogRecord
from aion.shared.metaclasses import Singleton


def _print_debug(message: str):
    """Simple debug output without using logging system"""
    print(f"[AionApiLogManager] {message}", file=sys.stderr)


class AionApiLogManager(metaclass=Singleton):
    def __init__(self, queue_size: int = 1000):
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.worker_task = None

        self._disabled = False
        self._started = False

        # Config
        self.url = None
        self.batch_size = 100
        self.flush_interval = 10
        self.client_id = None

        # Metrics
        self.sent_count = 0
        self.dropped_count = 0
        self.buffered_before_start = 0

    @property
    def is_disabled(self) -> bool:
        """Check if logging is explicitly disabled"""
        return self._disabled

    @property
    def is_started(self) -> bool:
        """Check if worker is started and running"""
        return self._started and self.worker_task is not None

    @property
    def can_accept_logs(self) -> bool:
        """Check if we should accept new logs"""
        return not self._disabled and self.queue is not None

    def add_log(self, record: AionLogRecord) -> bool:
        """
        Add log to queue. Returns True if accepted, False otherwise.
        Works even if worker not started yet - logs will be buffered.
        """
        if not self.can_accept_logs:
            return False

        try:
            self.queue.put_nowait(record)

            # Track buffered logs before worker starts
            if not self.is_started:
                self.buffered_before_start += 1

            return True

        except asyncio.QueueFull:
            self.dropped_count += 1
            return False
        except Exception as e:
            _print_debug(f"Error adding log: {e}")
            return False

    async def start(
            self,
            url: str,
            batch_size: int = 100,
            flush_interval: float = 10,
            client_id: Optional[str] = None
    ) -> bool:
        """
        Start the worker. Returns True on success, False on failure.
        On failure, disables the manager and clears queue.
        """
        if self._started:
            _print_debug("Worker already started")
            return True

        if self._disabled:
            _print_debug("Manager is disabled, cannot start")
            return False

        # Validate configuration
        if not url:
            _print_debug("No URL provided, disabling log manager")
            self.disable()
            return False

        if not client_id:
            _print_debug("No client_id provided, disabling log manager")
            self.disable()
            return False

        try:
            # Store config
            self.url = url
            self.batch_size = batch_size
            self.flush_interval = flush_interval
            self.client_id = client_id

            # Start worker
            self.worker_task = asyncio.create_task(self._worker())
            self._started = True
            return True

        except Exception as e:
            _print_debug(f"Failed to start worker: {e}")
            self.disable()
            return False

    def disable(self):
        """
        Disable manager - clear queue and set flag.
        Handler will stop sending logs.
        """
        self._disabled = True

        # Clear queue
        if self.queue is not None:
            try:
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                        self.queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                    except Exception:
                        break

                if self.buffered_before_start:
                    _print_debug(f"Queue cleared, dropped {self.buffered_before_start} buffered logs")

            except Exception as ex:
                _print_debug(f"Error clearing queue: {ex}")

        self.buffered_before_start = 0

    async def _worker(self):
        """Background worker"""
        batch = []

        try:
            while True:
                try:
                    entry = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=self.flush_interval
                    )
                    batch.append(entry)
                    self.queue.task_done()

                    if len(batch) >= self.batch_size:
                        await self._send_batch(batch)
                        batch.clear()

                except asyncio.TimeoutError:
                    if batch:
                        await self._send_batch(batch)
                        batch.clear()

                except asyncio.CancelledError:
                    # Graceful shutdown
                    if batch:
                        await self._send_batch(batch)
                    raise

        except asyncio.CancelledError:
            pass
        except Exception as ex:
            _print_debug(f"Worker crashed: {ex}")
            self.disable()

    async def _send_batch(self, batch: List[AionLogRecord]):
        """Send batch via HTTP"""
        try:
            # async with aiohttp.ClientSession() as session:
            #     await session.post(
            #         self.url,
            #         json=batch,
            #         timeout=aiohttp.ClientTimeout(total=30)
            #     )
            _print_debug(f"Aion: Sending {len(batch)} log entries")
            self.sent_count += len(batch)

        except Exception as ex:
            _print_debug(f"Failed to send batch: {ex}")

    async def shutdown(self, timeout: float = 5.0):
        """Graceful shutdown"""
        if not self.is_started:
            return

        try:
            # Wait for queue
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            _print_debug(f"Timeout waiting for queue, {self.queue.qsize()} logs will be lost")

        # Cancel worker
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        self._started = False
