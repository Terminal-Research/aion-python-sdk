import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from functools import partial
from typing import Any

import anyio
import sse_starlette
import sse_starlette.sse
import structlog.stdlib
from starlette.types import Receive, Scope, Send

from langgraph_api.asyncio import SimpleTaskGroup, aclosing
from langgraph_api.serde import json_dumpb

logger = structlog.stdlib.get_logger(__name__)


class EventSourceResponse(sse_starlette.EventSourceResponse):
    def __init__(
        self,
        content: AsyncIterator[bytes | tuple[bytes, Any | bytes]],
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content=content, status_code=status_code, headers=headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with anyio.create_task_group() as task_group:
            # https://trio.readthedocs.io/en/latest/reference-core.html#custom-supervisors
            async def wrap(func: Callable[[], Awaitable[None]]) -> None:
                await func()
                # noinspection PyAsyncCall
                task_group.cancel_scope.cancel()

            task_group.start_soon(wrap, partial(self.stream_response, send))
            task_group.start_soon(wrap, self.listen_for_exit_signal)

            if self.data_sender_callable:
                task_group.start_soon(self.data_sender_callable)

            await wrap(partial(self.listen_for_disconnect, receive))

        if self.background is not None:  # pragma: no cover, tested in StreamResponse
            await self.background()

    async def stream_response(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        async with (
            SimpleTaskGroup(sse_heartbeat(send), cancel=True, wait=False),
            aclosing(self.body_iterator) as body,
        ):
            try:
                async for data in body:
                    with anyio.move_on_after(self.send_timeout) as timeout:
                        await send(
                            {
                                "type": "http.response.body",
                                "body": json_to_sse(*data)
                                if isinstance(data, tuple)
                                else data,
                                "more_body": True,
                            }
                        )
                    if timeout.cancel_called:
                        raise sse_starlette.sse.SendTimeoutError()
            except sse_starlette.sse.SendTimeoutError:
                raise
            except Exception as exc:
                await logger.aexception("Error streaming response", exc_info=exc)
                await send(
                    {
                        "type": "http.response.body",
                        "body": json_to_sse(b"error", exc),
                        "more_body": False,
                    }
                )

        async with self._send_lock:
            self.active = False
            await send({"type": "http.response.body", "body": b"", "more_body": False})


async def sse_heartbeat(send: Send) -> None:
    payload = sse_starlette.ServerSentEvent(comment="heartbeat").encode()
    try:
        while True:
            await asyncio.sleep(5)
            await send(
                {"type": "http.response.body", "body": payload, "more_body": True}
            )
    except asyncio.CancelledError:
        pass


SEP = b"\r\n"
EVENT = b"event: "
DATA = b"data: "
BYTES_LIKE = (bytes, bytearray, memoryview)


def json_to_sse(event: bytes, data: Any | bytes) -> bytes:
    return b"".join(
        (
            EVENT,
            event,
            SEP,
            DATA,
            data if isinstance(data, BYTES_LIKE) else json_dumpb(data),
            SEP,
            SEP,
        )
    )
