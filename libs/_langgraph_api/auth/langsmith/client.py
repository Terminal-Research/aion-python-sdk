from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from httpx._types import HeaderTypes, QueryParamTypes, RequestData
from tenacity import retry
from tenacity.retry import retry_if_exception
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_exponential_jitter

from langgraph_api.config import LANGSMITH_AUTH_ENDPOINT

_client: "JsonHttpClient"


def is_retriable_error(exception: Exception) -> bool:
    if isinstance(exception, httpx.TransportError):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        if exception.response.status_code > 499:
            return True

    return False


retry_httpx = retry(
    reraise=True,
    retry=retry_if_exception(is_retriable_error),
    wait=wait_exponential_jitter(),
    stop=stop_after_attempt(3),
)


class JsonHttpClient:
    """HTTPX client for JSON requests, with retries."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Initialize the auth client."""
        self.client = client

    async def _get(
        self,
        path: str,
        *,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.get(path, params=params, headers=headers)

    @retry_httpx
    async def get(
        self,
        path: str,
        *,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.get(path, params=params, headers=headers)

    async def _post(
        self,
        path: str,
        *,
        data: RequestData | None = None,
        json: Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            path, data=data, json=json, params=params, headers=headers
        )

    @retry_httpx
    async def post(
        self,
        path: str,
        *,
        data: RequestData | None = None,
        json: Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            path, data=data, json=json, params=params, headers=headers
        )


def create_client() -> JsonHttpClient:
    """Create the auth http client."""
    return JsonHttpClient(
        httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                retries=5,  # this applies only to ConnectError, ConnectTimeout
                limits=httpx.Limits(
                    max_keepalive_connections=40,
                    keepalive_expiry=240.0,
                ),
            ),
            timeout=httpx.Timeout(2.0),
            base_url=LANGSMITH_AUTH_ENDPOINT,
        )
    )


async def close_auth_client() -> None:
    """Close the auth http client."""
    global _client
    try:
        await _client.client.aclose()
    except NameError:
        pass


async def initialize_auth_client() -> None:
    """Initialize the auth http client."""
    await close_auth_client()
    global _client
    _client = create_client()


@asynccontextmanager
async def auth_client() -> AsyncGenerator[JsonHttpClient, None]:
    """Get the auth http client."""
    # pytest does something funny with event loops,
    # so we can't use a global pool for tests
    if LANGSMITH_AUTH_ENDPOINT.startswith("http://localhost"):
        client = create_client()
        try:
            yield client
        finally:
            await client.client.aclose()
    else:
        try:
            if not _client.client.is_closed:
                found = True
            else:
                found = False
        except NameError:
            found = False
        if found:
            yield _client
        else:
            await initialize_auth_client()
            yield _client
