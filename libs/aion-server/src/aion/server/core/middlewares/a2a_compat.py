import json
from typing import Callable

from a2a.utils import DEFAULT_RPC_URL

from aion.server.compat import A2AV03Adapter

__all__ = ["A2ACompatMiddleware"]


class A2ACompatMiddleware:
    """
    Pure ASGI middleware that rewrites incoming v1.0 JSON-RPC requests to v0.3
    wire format by replacing the ASGI receive callable before any downstream
    middleware or handler reads the body.

    Must be registered last in the middleware stack so it executes first.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("method") == "POST" and scope.get("path") == DEFAULT_RPC_URL:
            receive = await self._rewrite_receive(receive)
        await self.app(scope, receive, send)

    @staticmethod
    async def _rewrite_receive(receive: Callable) -> Callable:
        # Drain the full body from the original receive stream.
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        try:
            data = json.loads(body)
            data = A2AV03Adapter.transform_request(data)
            body = json.dumps(data, ensure_ascii=False).encode()
        except Exception:
            pass

        body_consumed = False

        async def patched_receive():
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Delegate to original receive for subsequent calls (e.g. http.disconnect)
            return await receive()

        return patched_receive
