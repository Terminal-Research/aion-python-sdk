"""Attach frozen runtime identity at the process producing an HTTP response."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aion.core.deployment import RUNTIME_ATTEMPT_HEADER, RuntimeIdentity


class RuntimeIdentityMiddleware:
    """Identify origin responses without consuming or changing their bodies.

    Args:
        app: Wrapped application.
        identity: Snapshot captured before this application starts serving.
        paths: Optional exact origin paths. The proxy restricts these so its
            identity never replaces or supplies proof for a forwarded response.
    """

    def __init__(
        self,
        app: ASGIApp,
        identity: RuntimeIdentity,
        paths: frozenset[str] | None = None,
    ) -> None:
        self.app = app
        self.identity = identity
        self.paths = paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add the startup attempt to response headers for owned HTTP routes."""
        if (scope["type"] != "http"
                or (self.paths is not None and scope["path"] not in self.paths)):
            await self.app(scope, receive, send)
            return

        async def send_identified(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[RUNTIME_ATTEMPT_HEADER] = str(
                    self.identity.runtime_attempt_id)
            await send(message)

        await self.app(scope, receive, send_identified)
