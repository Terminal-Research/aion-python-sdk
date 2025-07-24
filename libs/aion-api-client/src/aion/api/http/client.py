import logging
from typing import Optional, Dict, Any

import httpx

from aion.api.exceptions import AionAuthenticationError
from aion.api.config import aion_api_settings

logger = logging.getLogger(__name__)


class AionHttpApiClient:
    """
    Simple HTTP client for Aion API without built-in token management.

    This client provides basic HTTP functionality for communicating with the Aion API,
    including authentication and general request handling.
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def authenticate(self) -> Dict[str, Any]:
        """
        Authenticate with the Aion API and return token data.

        Performs client credentials authentication using the configured client ID
        and secret from aion_api_settings. Returns the full authentication response
        which typically contains the access token and other metadata.
        """
        if not aion_api_settings.client_id or not aion_api_settings.client_secret:
            raise ValueError("AION_CLIENT_ID and AION_CLIENT_SECRET must be set")

        payload = {
            "clientId": aion_api_settings.client_id,
            "secret": aion_api_settings.client_secret
        }

        response = await self.request(
            method="POST",
            endpoint="/auth/token",
            token=None,
            json_data=payload)

        if response.status_code == 401:
            raise AionAuthenticationError("Invalid client credentials")
        if response.status_code != 200:
            raise AionAuthenticationError(f"Authentication failed: {response.status_code}")

        return response.json()

    async def request(
            self,
            method: str,
            endpoint: str,
            token: Optional[str] = None,
            json_data: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None
    ) -> httpx.Response:
        """
        Make an HTTP request to the Aion API with optional authentication.

        Constructs the full URL using the configured API base URL and provided endpoint,
        then makes an async HTTP request with optional Bearer token authentication.

        Args:
            method (str): HTTP method (GET, POST, PUT, DELETE, etc.)
            endpoint (str): API endpoint path (e.g., '/users', '/auth/token')
            token (Optional[str]): Bearer token for authentication
            json_data (Optional[Dict[str, Any]]): JSON payload for the request body
            params (Optional[Dict[str, Any]]): URL query parameters
            headers (Optional[Dict[str, str]]): Additional HTTP headers

        Returns:
            httpx.Response: Raw HTTP response object

        Raises:
            httpx.RequestError: If the request fails due to network issues
            httpx.HTTPStatusError: If the server returns an HTTP error status
        """
        url = f"{aion_api_settings.http_url}{endpoint}"
        request_headers = {}

        if token:
            request_headers["Authorization"] = f"Bearer {token}"  # todo not sure about token format

        if headers:
            request_headers.update(headers)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(
                method=method,
                url=url,
                json=json_data,
                params=params,
                headers=request_headers
            )
