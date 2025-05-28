import functools
import inspect
import typing

import jsonschema_rs
import orjson
from starlette._exception_handler import wrap_app_handling_exceptions
from starlette._utils import is_async_callable
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, compile_path, get_name
from starlette.types import ASGIApp, Receive, Scope, Send

from langgraph_api.serde import json_dumpb
from langgraph_api.utils import get_auth_ctx, with_user


def api_request_response(
    func: typing.Callable[[Request], typing.Awaitable[Response] | Response],
) -> ASGIApp:
    """
    Takes a function or coroutine `func(request) -> response`,
    and returns an ASGI application.
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        request = ApiRequest(scope, receive, send)

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if is_async_callable(func):
                response = await func(request)
            else:
                response = await run_in_threadpool(func, request)
            await response(scope, receive, send)

        await wrap_app_handling_exceptions(app, request)(scope, receive, send)

    return app


class ApiResponse(JSONResponse):
    def render(self, content: typing.Any) -> bytes:
        return json_dumpb(content)


def _json_loads(
    content: bytearray, schema: jsonschema_rs.Draft4Validator | None
) -> typing.Any:
    json = orjson.loads(content)
    if schema is not None:
        schema.validate(json)
    return json


class ApiRequest(Request):
    async def body(self) -> typing.Coroutine[typing.Any, typing.Any, bytearray]:
        if not hasattr(self, "_body"):
            chunks = bytearray()
            async for chunk in self.stream():
                chunks.extend(chunk)
            self._body = chunks
        return self._body

    async def json(self, schema: jsonschema_rs.Draft4Validator | None) -> typing.Any:
        if not hasattr(self, "_json"):
            body = await self.body()
            self._json = await run_in_threadpool(_json_loads, body, schema)
        return self._json


# ApiRoute uses our custom ApiRequest class to handle requests.
class ApiRoute(Route):
    def __init__(
        self,
        path: str,
        endpoint: typing.Callable[..., typing.Any],
        *,
        methods: list[str] | None = None,
        name: str | None = None,
        include_in_schema: bool = True,
        middleware: typing.Sequence[Middleware] | None = None,
    ) -> None:
        assert path.startswith("/"), "Routed paths must start with '/'"
        self.path = path
        self.endpoint = endpoint
        self.name = get_name(endpoint) if name is None else name
        self.include_in_schema = include_in_schema

        endpoint_handler = endpoint
        while isinstance(endpoint_handler, functools.partial):
            endpoint_handler = endpoint_handler.func
        if inspect.isfunction(endpoint_handler) or inspect.ismethod(endpoint_handler):
            # Endpoint is function or method. Treat it as `func(request) -> response`.
            self.app = api_request_response(endpoint)
            if methods is None:
                methods = ["GET"]
        else:
            # Endpoint is a class. Treat it as ASGI.
            self.app = endpoint

        if middleware is not None:
            for cls, args, kwargs in reversed(middleware):
                self.app = cls(app=self.app, *args, **kwargs)  # noqa: B026

        if methods is None:
            self.methods = None
        else:
            self.methods = {method.upper() for method in methods}
            if "GET" in self.methods:
                self.methods.add("HEAD")

        self.path_regex, self.path_format, self.param_convertors = compile_path(path)

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # https://asgi.readthedocs.io/en/latest/specs/www.html#http-connection-scope
        scope["route"] = self.path
        ctx = get_auth_ctx()
        if ctx:
            user, auth = ctx.user, ctx.permissions
        else:
            user, auth = scope.get("user"), scope.get("auth")
        async with with_user(user, auth):
            return await super().handle(scope, receive, send)
