"""A lightweight HTTP callback server for push notification scenarios.

Starts on a free port in a background thread with its own event loop,
records every POST it receives, and lets a test wait until a notification
arrives.  The separate thread ensures the callback handler runs even when
the test's own event loop is busy reading an SSE stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["CallbackServer"]


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


HTTP_200 = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Length: 0\r\n"
    b"Connection: close\r\n"
    b"\r\n"
)


@dataclass
class Notification:
    """One POST the callback server received."""

    body: dict[str, Any]
    headers: dict[str, str]


class CallbackServer:
    """An HTTP server that accepts push notification callbacks.

    Runs in a dedicated background thread so the test's event loop does not
    need to be idle for the handler to execute.

    Usage::

        server = await CallbackServer.start()
        try:
            # ... register server.url as push notification target ...
            notifications = await server.wait(timeout=10)
            assert notifications
        finally:
            await server.stop()
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}/callback"
        self.notifications: list[Notification] = []
        self._lock = threading.Lock()
        self._arrived = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.AbstractServer] = None

    @classmethod
    async def start(cls) -> "CallbackServer":
        """Start the callback server on a free port in a background thread."""
        port = _free_port()
        instance = cls(port)
        ready = threading.Event()
        instance._thread = threading.Thread(
            target=instance._run_loop, args=(ready,), daemon=True
        )
        instance._thread.start()
        ready.wait(timeout=10)
        return instance

    def _run_loop(self, ready: threading.Event) -> None:
        """Entry point of the background thread: run the server loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve(ready))

    async def _serve(self, ready: threading.Event) -> None:
        """Start the TCP server and run until told to stop."""
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", self.port
        )
        ready.set()
        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            pass

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read one HTTP request and answer 200."""
        try:
            raw = await asyncio.wait_for(reader.read(1024 * 1024), timeout=10)
            headers_part, _, body = raw.partition(b"\r\n\r\n")
            header_lines = headers_part.decode("utf-8", errors="replace").split("\r\n")

            parsed_headers: dict[str, str] = {}
            for line in header_lines[1:]:
                if ": " in line:
                    key, _, value = line.partition(": ")
                    parsed_headers[key] = value

            content_length = int(
                parsed_headers.get("Content-Length",
                                   parsed_headers.get("content-length", "0"))
            )
            while len(body) < content_length:
                chunk = await asyncio.wait_for(
                    reader.read(content_length - len(body)), timeout=10
                )
                if not chunk:
                    break
                body += chunk

            try:
                parsed_body = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed_body = {}

            with self._lock:
                self.notifications.append(
                    Notification(body=parsed_body, headers=parsed_headers)
                )
            self._arrived.set()

            writer.write(HTTP_200)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def wait(self, *, timeout: float = 15.0, count: int = 1) -> list[Notification]:
        """Wait until at least ``count`` notifications have arrived.

        Polls from the calling (test) event loop via a short sleep, since the
        notifications are recorded in a different thread.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            with self._lock:
                if len(self.notifications) >= count:
                    return list(self.notifications)
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                with self._lock:
                    return list(self.notifications)
            await asyncio.sleep(min(0.1, remaining))

    async def stop(self) -> None:
        """Shut down the server and join the background thread."""
        if self._server is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None
