"""Request handlers for the AION Agent Proxy Server."""

import logging
from typing import Any, AsyncIterator, Dict
from urllib.parse import urljoin

import httpx
from fastapi import Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from .exceptions import (
    AgentNotFoundException,
    AgentUnavailableException,
    AgentTimeoutException,
    AgentProxyException
)
from .types import AgentHealthInfo

logger = logging.getLogger(__name__)

# Upstream timeout for forwarded requests: connect/write/pool stay bounded,
# but read is unbounded - streamed agent responses (SSE task updates) may
# legitimately stay silent between chunks longer than any fixed limit.
_FORWARD_TIMEOUT = httpx.Timeout(30.0, read=None)

# Headers that describe the *client's* connection to the proxy and say nothing
# about the proxy's own connection upstream. `host` would address the wrong
# server; the framing headers (RFC 9110 7.6.1) describe a hop that ends here -
# forwarding `content-length` or `transfer-encoding` alongside a body httpx
# re-frames itself is how a request ends up declaring two different lengths.
_REQUEST_HEADERS_MANAGED_BY_PROXY = frozenset({
    'host',
    'connection',
    'content-length',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
})

_RESPONSE_HEADERS_MANAGED_BY_PROXY = frozenset({
    'connection',
    'content-length',
    'date',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'server',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
})


class UpstreamStreamingResponse(StreamingResponse):
    """Stream one HTTPX response and always release its connection.

    Starlette normally runs response background tasks after streaming. A
    downstream disconnect can instead raise before those tasks run, so this
    response closes the upstream connection from a ``finally`` block.
    """

    def __init__(
        self,
        upstream_response: httpx.Response,
        headers: Dict[str, str],
        agent_id: str,
    ) -> None:
        """Initialize a response backed by the upstream raw byte stream.

        Args:
            upstream_response: Open HTTPX response to forward.
            headers: End-to-end response headers safe to forward downstream.
            agent_id: Target agent identifier, used when logging stream errors.
        """
        self._upstream_response = upstream_response
        self._agent_id = agent_id
        super().__init__(
            content=self._relay_upstream(),
            status_code=upstream_response.status_code,
            headers=headers,
        )

    async def _relay_upstream(self) -> AsyncIterator[bytes]:
        """Yield upstream body bytes, ending the body on a transport failure.

        ``aiter_raw`` skips HTTPX content decoding, so the forwarded bytes stay
        consistent with the upstream headers - including ``content-encoding``.
        A failure mid-stream cannot be reported downstream: the response status
        and headers are already on the wire, so it is logged and the body ends.

        Yields:
            Raw response chunks in the order the agent produced them.
        """
        try:
            async for chunk in self._upstream_response.aiter_raw():
                yield chunk
        except httpx.HTTPError as e:
            logger.error(
                f"Error streaming response from agent "
                f"'{self._agent_id}': {str(e)}"
            )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Send the response and close its upstream stream afterward."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream_response.aclose()


class RequestHandler:
    """Handles request forwarding to agent servers"""

    def __init__(self, agent_urls: Dict[str, str], http_client: httpx.AsyncClient):
        """
        Initialize request handler

        Args:
            agent_urls: Mapping of agent_id to agent base URLs
            http_client: HTTP client for making requests
        """
        self.agent_urls = agent_urls
        self.http_client = http_client

    async def check_agents_health(self) -> Dict[str, Any]:
        """
        Check health status of all configured agents

        Returns:
            Dictionary with status of each agent compatible with SystemHealthResponse
        """
        results = {}

        for agent_id, agent_url in self.agent_urls.items():
            try:
                # Try to connect to agent's health endpoint or root
                response = await self.http_client.get(
                    f"{agent_url}/health/",
                    timeout=5.0
                )
                results[agent_id] = AgentHealthInfo(
                    status="healthy" if response.status_code == 200 else "unhealthy",
                    url=agent_url,
                    status_code=response.status_code
                )
            except httpx.ConnectError:
                results[agent_id] = AgentHealthInfo(
                    status="unavailable",
                    url=agent_url,
                    error="connection_refused"
                )
            except httpx.TimeoutException:
                results[agent_id] = AgentHealthInfo(
                    status="timeout",
                    url=agent_url,
                    error="timeout"
                )
            except Exception as e:
                results[agent_id] = AgentHealthInfo(
                    status="error",
                    url=agent_url,
                    error=str(e)
                )

        # Overall status
        all_healthy = all(agent.status == "healthy" for agent in results.values())

        return {
            "proxy_status": "healthy",
            "overall_agents_status": "healthy" if all_healthy else "degraded",
            "agents": results
        }

    async def forward_request(self, agent_id: str, path: str, request: Request) -> Response:
        """
        Forward the incoming request to the target agent, streaming the
        response body through as it arrives.

        The upstream response is not buffered: each chunk is relayed to the
        client as soon as the agent produces it, so streaming transports
        (e.g. SSE task updates from SendStreamingMessage) deliver
        intermediate events in real time instead of one batch at completion.

        Args:
            agent_id: Target agent identifier
            path: Path to forward to the agent
            request: Incoming FastAPI request

        Returns:
            Streaming response relaying the target agent's response

        Raises:
            AgentNotFoundException: When agent_id is not found
            AgentUnavailableException: When agent server is unreachable
            AgentTimeoutException: When agent server times out
            AgentProxyException: When there's an error forwarding the request
        """
        # Check if agent exists
        if agent_id not in self.agent_urls:
            available_agents = list(self.agent_urls.keys())
            raise AgentNotFoundException(agent_id, available_agents)

        # Build target URL
        agent_base_url = self.agent_urls[agent_id]
        target_url = urljoin(f"{agent_base_url}/", path)

        # Add query parameters if present
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        try:
            # Prepare headers: everything the client sent except what described
            # its own hop to us (see _REQUEST_HEADERS_MANAGED_BY_PROXY). httpx
            # frames the upstream request itself from `content`.
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in _REQUEST_HEADERS_MANAGED_BY_PROXY
            }

            # Read request body
            body = await request.body()

            # Keep the upstream response open so streaming responses can flow
            # through the proxy without first being buffered in memory.
            upstream_request = self.http_client.build_request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                timeout=_FORWARD_TIMEOUT,
            )
            response = await self.http_client.send(
                upstream_request,
                stream=True,
            )

            return UpstreamStreamingResponse(
                response,
                self._forwarded_response_headers(response),
                agent_id,
            )

        except httpx.ConnectError:
            logger.error(f"Failed to connect to agent '{agent_id}' at {agent_base_url}")
            raise AgentUnavailableException(agent_id)

        except httpx.TimeoutException:
            logger.error(f"Timeout when connecting to agent '{agent_id}'")
            raise AgentTimeoutException(agent_id)

        except Exception as e:
            logger.error(f"Error forwarding request to agent '{agent_id}': {str(e)}")
            raise AgentProxyException(agent_id, str(e))

    @staticmethod
    def _forwarded_response_headers(
        response: httpx.Response,
    ) -> Dict[str, str]:
        """Select headers that remain valid after proxy reframing.

        The proxy consumes upstream transfer framing and lets the downstream
        ASGI server establish new framing. It must therefore omit the original
        content length, transfer encoding, and other hop-by-hop headers.

        Args:
            response: Open upstream HTTP response.

        Returns:
            End-to-end headers safe to attach to the downstream response.
        """
        connection_headers = {
            value.strip().lower()
            for value in response.headers.get('connection', '').split(',')
            if value.strip()
        }
        excluded = _RESPONSE_HEADERS_MANAGED_BY_PROXY | connection_headers
        return {
            name: value
            for name, value in response.headers.items()
            if name.lower() not in excluded
        }
