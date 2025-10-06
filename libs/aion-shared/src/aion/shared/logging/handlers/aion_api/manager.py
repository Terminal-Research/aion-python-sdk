import asyncio
from typing import Optional, List

from aion.shared.logging.base import AionLogRecord
from aion.shared.metaclasses import Singleton
from aion.shared.settings import platform_settings
from aion.shared.utils import create_logstash_log_entry
from .client import AionLogstashClient


class AionApiLogManager(metaclass=Singleton):
    def __init__(self, queue_size: int = 1000):
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.worker_task = None
        self.client: Optional[AionLogstashClient] = None

        self._disabled = False
        self._started = False
        self._shutdown_event = asyncio.Event()

        # Config
        self.url = None
        self.batch_size = 100
        self.flush_interval = 10
        self.client_id = None
        self.client_timeout = 5

        # Metrics
        self.sent_count = 0
        self.dropped_count = 0
        self.buffered_before_start = 0
        self.__logger = None

    @property
    def _logger(self):
        """Lazy logger initialization to avoid circular imports"""
        if self.__logger is None:
            from aion.shared.logging.factory import get_logger
            self.__logger = get_logger(use_aion_api=False)
        return self.__logger

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
        return not self._disabled and not self._shutdown_event.is_set() and self.queue is not None

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
            self._logger.warning(f"Queue full, log dropped (total dropped: {self.dropped_count})")
            return False
        except Exception as ex:
            self._logger.exception(f"Error adding log: {ex}")
            return False

    async def start(
            self,
            url: str,
            batch_size: int = 100,
            flush_interval: float = 10,
            client_id: Optional[str] = None,
            client_timeout: int = 5
    ) -> bool:
        """
        Start the worker. Returns True on success, False on failure.
        On failure, disables the manager and clears queue.
        """
        if self._started:
            self._logger.warning("Worker already started")
            return True

        if self._disabled:
            self._logger.warning("Manager is disabled, cannot start")
            return False

        # Validate configuration
        if not url:
            self._logger.warning("No URL provided, disabling log manager")
            self.disable()
            return False

        if not client_id:
            self._logger.warning("No client_id provided, disabling log manager")
            self.disable()
            return False

        try:
            # Store config
            self.url = url
            self.batch_size = batch_size
            self.flush_interval = flush_interval
            self.client_id = client_id
            self.client_timeout = client_timeout

            # Initialize client
            self.client = AionLogstashClient(url=url, timeout=client_timeout)

            # Reset shutdown flag in case of restart
            self._shutdown_event.clear()

            # Start worker
            self.worker_task = asyncio.create_task(self._worker())
            self._started = True
            return True

        except Exception as ex:
            self._logger.exception(f"Failed to start worker: {ex}")
            await self._cleanup_client()
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
                    self._logger.warning(f"Queue cleared, dropped {self.buffered_before_start} buffered logs")

            except Exception as ex:
                self._logger.exception(f"Error clearing queue: {ex}")

        self.buffered_before_start = 0

    async def _worker(self):
        """Background worker that periodically checks shutdown event"""
        batch = []
        check_interval = min(self.flush_interval, 1.0)  # Check shutdown at least every second

        try:
            while True:
                # Check if shutdown requested
                if self._shutdown_event.is_set():
                    self._logger.info("Shutdown event detected, draining remaining logs")
                    break

                try:
                    entry = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=check_interval
                    )
                    batch.append(entry)
                    self.queue.task_done()

                    if len(batch) >= self.batch_size:
                        await self._send_batch(batch)
                        batch.clear()

                except asyncio.TimeoutError:
                    # Timeout reached - check if we have batch to send
                    if batch:
                        await self._send_batch(batch)
                        batch.clear()

                    # Continue loop to check shutdown event

                except asyncio.CancelledError:
                    # Graceful shutdown via task cancellation
                    self._logger.info("Worker task cancelled, sending remaining batch")
                    if batch:
                        await self._send_batch(batch)
                    raise

            # Shutdown requested - drain queue and process all remaining items
            self._logger.info(
                f"Draining queue (current batch: {len(batch)}, queue size: {self.queue.qsize()})")

            # First, send current batch if exists
            if batch:
                await self._send_batch(batch)
                batch.clear()

            # Then process all remaining items in queue
            while not self.queue.empty():
                try:
                    entry = self.queue.get_nowait()
                    batch.append(entry)
                    self.queue.task_done()

                    # Send in batches to avoid memory issues
                    if len(batch) >= self.batch_size:
                        await self._send_batch(batch)
                        batch.clear()

                except asyncio.QueueEmpty:
                    break
                except Exception as ex:
                    self._logger.exception(f"Error draining queue: {ex}")
                    break

            # Send final batch
            if batch:
                await self._send_batch(batch)
                batch.clear()

            self._logger.info("Queue drained successfully")

        except asyncio.CancelledError:
            self._logger.info("Worker task cancelled")
            # Even on cancellation, try to send what we have
            if batch:
                try:
                    await self._send_batch(batch)
                except Exception:
                    pass
        except Exception as ex:
            self._logger.exception(f"Worker crashed: {ex}")
            self.disable()

    async def _send_batch(self, batch: List[AionLogRecord]):
        """Send batch via HTTP using AionLogstashClient"""
        if not self.client:
            self._logger.error("Client not initialized")
            return

        try:
            # Convert AionLogRecord objects to dicts
            log_dicts = [self._prepare_record(record) for record in batch]

            # Send via client
            success = await self.client.send(log_dicts)
            if success:
                self.sent_count += len(batch)

        except Exception as ex:
            self._logger.exception(f"Failed to send batch: {ex}")

    def _prepare_record(self, record: AionLogRecord) -> dict:
        """Convert AionLogRecord to dict for sending"""
        return create_logstash_log_entry(
            record=record,
            client_id=self.client_id,
            node_name=platform_settings.node_name)

    async def _cleanup_client(self):
        """Close client session"""
        if self.client:
            try:
                await self.client.close_session()
                self._logger.debug("Client session closed")
            except Exception as ex:
                self._logger.exception(f"Error closing client session: {ex}")
            finally:
                self.client = None

    async def shutdown(self, timeout: float = 5.0):
        """
        Graceful shutdown with proper task cleanup.

        Args:
            timeout: Maximum time to wait for queue processing
        """
        if not self.is_started:
            self._logger.info("Manager not started, nothing to shutdown")
            return

        self._logger.info(f"Starting shutdown (queue size: {self.queue.qsize()})")

        # Set shutdown event - worker will check it
        self._shutdown_event.set()

        try:
            # Try to process remaining items in queue
            try:
                await asyncio.wait_for(self.queue.join(), timeout=timeout)
                self._logger.info("All queued logs processed")
            except asyncio.TimeoutError:
                remaining = self.queue.qsize()
                self._logger.warning(f"Shutdown timeout, {remaining} logs will be lost")

            # Wait for worker to finish naturally (it checks shutdown flag)
            if self.worker_task and not self.worker_task.done():
                self._logger.info("Waiting for worker task to complete")
                try:
                    # Give worker a bit more time to finish cleanly
                    await asyncio.wait_for(self.worker_task, timeout=2.0)
                    self._logger.info("Worker task completed naturally")
                except asyncio.TimeoutError:
                    # If still running, cancel it
                    self._logger.warning("Worker task timeout, cancelling")
                    self.worker_task.cancel()
                    try:
                        await self.worker_task
                    except asyncio.CancelledError:
                        self._logger.info("Worker task cancelled")
                except asyncio.CancelledError:
                    self._logger.info("Worker task cancelled")
                except Exception as ex:
                    self._logger.exception(f"Error during worker completion: {ex}")

        except Exception as ex:
            self._logger.exception(f"Error during shutdown: {ex}")

        finally:
            # Close client session
            await self._cleanup_client()

            # Always cleanup state
            self.worker_task = None
            self._started = False
            self._logger.info(f"Shutdown complete (sent: {self.sent_count}, dropped: {self.dropped_count})")
