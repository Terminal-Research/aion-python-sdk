import asyncio
import logging

import structlog
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope, Send

from langgraph_api.asyncio import create_task
from langgraph_api.logging import LOG_JSON

asgi = structlog.stdlib.get_logger("asgi")

PATHS_IGNORE = {"/ok", "/metrics"}


class AccessLoggerMiddleware:
    def __init__(
        self,
        app,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        self.app = app
        self.logger = logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (LOG_JSON and scope.get("path") in PATHS_IGNORE):
            return await self.app(scope, receive, send)  # pragma: no cover

        loop = asyncio.get_event_loop()
        info = {"response": {}}

        if self.logger.isEnabledFor(logging.DEBUG):

            async def inner_receive() -> Message:
                message = await receive()
                asgi.debug(f"ASGI receive {message['type']}", **message)
                return message

            async def inner_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    info["response"] = message
                await send(message)
                asgi.debug(f"ASGI send {message['type']}", **message)
        else:
            inner_receive = receive

            async def inner_send(message) -> None:
                if message["type"] == "http.response.start":
                    info["response"] = message
                await send(message)

        try:
            info["start_time"] = loop.time()
            await self.app(scope, inner_receive, inner_send)
        except ClientDisconnect as exc:
            info["response"]["status"] = 499
            raise exc
        except Exception as exc:
            info["response"]["status"] = 500
            raise exc
        finally:
            info["end_time"] = loop.time()
            latency = int((info["end_time"] - info["start_time"]) * 1_000)
            create_task(
                self.logger.ainfo(
                    f"{scope.get('method')} {scope.get('path')} {info['response'].get('status')} {latency}ms",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status=info["response"].get("status"),
                    latency_ms=latency,
                    route=scope.get("route"),
                    path_params=scope.get("path_params"),
                    query_string=scope.get("query_string").decode(),
                    proto=scope.get("http_version"),
                    req_header=_headers_to_dict(scope.get("headers")),
                    res_header=_headers_to_dict(info["response"].get("headers")),
                )
            )


HEADERS_IGNORE = {b"authorization", b"cookie", b"set-cookie", b"x-api-key"}


def _headers_to_dict(headers: list[tuple[bytes, bytes]] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {
        k.decode(): v.decode() for k, v in headers if k.lower() not in HEADERS_IGNORE
    }
