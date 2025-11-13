"""
HTTP client management for AION Agent Proxy Server
"""
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from aion.shared.logging import get_logger

logger = get_logger()


class ProxyHttpClient:
    """Manages HTTP client lifecycle for proxy server"""

    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None

    @asynccontextmanager
    async def lifespan(self):
        """
        Context manager for HTTP client lifecycle

        Yields:
            httpx.AsyncClient: The initialized HTTP client
        """
        # Startup
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True
        )
        logger.debug("HTTP client initialized")

        try:
            yield self.client
        finally:
            # Shutdown
            if self.client:
                await self.client.aclose()
                logger.debug("HTTP client closed")

    def get_client(self) -> httpx.AsyncClient:
        """
        Get the current HTTP client instance

        Returns:
            httpx.AsyncClient: The HTTP client

        Raises:
            RuntimeError: If client is not initialized
        """
        if self.client is None:
            raise RuntimeError("HTTP client not initialized")
        return self.client
