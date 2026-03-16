import json
from typing import Callable

from a2a.utils import DEFAULT_RPC_URL
from aion.shared.logging import get_logger

from aion.server.compat import A2AV03Adapter

__all__ = ["A2ACompatMiddleware"]

logger = get_logger()

_SUPPORTED_VERSIONS = frozenset({"0.3", "1.0"})
_FRONTIER_VERSION = "1.0"

# -32600 is the standard JSON-RPC "Invalid Request" code.
# -32009: VersionNotSupportedError is an official A2A error (9th in the spec list).
# The a2a-sdk 0.3.x defines -32001..-32007 for errors 1-7; errors 8-9
# (ExtensionSupportRequiredError, VersionNotSupportedError) are not yet in the SDK.
# -32009 follows the sequential pattern and should match the official code once
# a2a-sdk >= 1.0 ships VersionNotSupportedError.
_INVALID_REQUEST_CODE = -32600
_VERSION_NOT_SUPPORTED_CODE = -32009


# TODO: Remove this middleware once a2a-sdk >= 1.0 is released.
# This adapter exists solely to downgrade v1.0 wire format to v0.3
# so the current SDK can process requests natively.
class A2ACompatMiddleware:
    """
    Pure ASGI middleware that bridges A2A protocol versions until a2a-sdk >= 1.0 is released.

    The server operates on v1.0 protocol internally, but the current a2a-sdk (0.3.x)
    only speaks v0.3 wire format. This middleware transparently downgrades v1.0 requests
    to v0.3 so the SDK can process them natively.

    Request handling:
      1. Reads the ``A2A-Version`` request header (assumes frontier version when absent).
      2. Returns a ``VersionNotSupportedError`` (-32009) for unrecognised versions.
      3. Passes v0.3 requests through unchanged (a2a-sdk speaks v0.3 natively).
      4. Rewrites v1.0 JSON-RPC request bodies to v0.3 wire format via ``A2AV03Adapter``.
      5. Returns an ``InvalidRequestError`` (-32600) if body transformation fails.

    Must be registered last in the middleware stack so it executes first.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != DEFAULT_RPC_URL:
            await self.app(scope, receive, send)
            return

        version = self._extract_version_header(scope)

        if version not in _SUPPORTED_VERSIONS:
            logger.warning(
                "%s %s | 400 VersionNotSupported: A2A version '%s' is not supported. Supported: %s",
                scope["method"],
                scope["path"],
                version,
                sorted(_SUPPORTED_VERSIONS),
            )
            await _send_jsonrpc_error(
                send,
                code=_VERSION_NOT_SUPPORTED_CODE,
                message=(
                    f"A2A protocol version '{version}' is not supported. "
                    f"Supported versions: {sorted(_SUPPORTED_VERSIONS)}"
                ),
            )
            return

        # Propagate the resolved version to downstream handlers via ASGI scope.
        scope["a2a_version"] = version

        if version == "1.0":
            result = await self._rewrite_receive(receive)
            if isinstance(result, _TransformError):
                logger.warning(
                    "%s %s | 400 InvalidRequest (id=%s): %s",
                    scope["method"],
                    scope["path"],
                    result.request_id,
                    result.message,
                )
                await _send_jsonrpc_error(
                    send,
                    code=_INVALID_REQUEST_CODE,
                    message=f"Invalid request: {result.message}",
                    request_id=result.request_id,
                )
                return
            receive = result

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_version_header(scope) -> str:
        """Return the A2A-Version header value, or the frontier version when absent."""
        for name, value in scope.get("headers", []):
            if name.lower() == b"a2a-version":
                return value.decode("utf-8").strip()
        return _FRONTIER_VERSION

    @staticmethod
    async def _rewrite_receive(receive: Callable) -> "Callable | _TransformError":
        """Drain the request body, transform v1.0 → v0.3, and return a patched receive callable.

        Returns a ``_TransformError`` instead of raising so the caller can send
        a proper JSON-RPC error response before discarding the connection.
        """
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        request_id = None
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                request_id = data.get("id")
            data = A2AV03Adapter.transform_request(data)
            body = json.dumps(data, ensure_ascii=False).encode()
        except json.JSONDecodeError as exc:
            return _TransformError(request_id=request_id, message=f"JSON parse error: {exc}")
        except Exception as exc:
            return _TransformError(request_id=request_id, message=str(exc))

        body_consumed = False

        async def patched_receive():
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Delegate subsequent calls (e.g. http.disconnect) to the original receive.
            return await receive()

        return patched_receive


class _TransformError:
    __slots__ = ("request_id", "message")

    def __init__(self, request_id, message: str) -> None:
        self.request_id = request_id
        self.message = message


async def _send_jsonrpc_error(send, *, code: int, message: str, request_id=None) -> None:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    ).encode()
    await send({
        "type": "http.response.start",
        "status": 400,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})
