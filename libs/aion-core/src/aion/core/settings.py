"""Environment-based settings for Aion API connections.

Provides Pydantic settings models that read connection parameters from
environment variables and .env files.
"""

from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "BaseEnvSettings",
    "ApiSettings",
    "api_settings",
]


class BaseEnvSettings(BaseSettings):
    """Base class for env settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


class ApiSettings(BaseEnvSettings):
    """
    Configuration class for Aion API connection settings.

    This class manages HTTP and WebSocket connection parameters for the Aion API.
    It provides lazy-loaded URL properties for both HTTP and WebSocket connections.
    """
    client_id: Optional[str] = Field(
        default=None,
        alias="AION_CLIENT_ID",
        description="Client ID for API authentication"
    )

    client_secret: Optional[str] = Field(
        default=None,
        alias="AION_CLIENT_SECRET",
        description="Client secret for API authentication"
    )

    api_host: str = Field(
        default="https://api.aion.to",
        alias="AION_API_HOST",
        description="API host URL"
    )

    api_keep_alive: int = Field(
        default=60,
        alias="AION_API_KEEP_ALIVE",
        description="Keep alive interval in seconds"
    )

    _gql_url: Optional[str] = None
    _ws_gql_url: Optional[str] = None
    _http_url: Optional[str] = None

    @field_validator("api_host")
    @classmethod
    def validate_api_host(cls, v):
        """Validate API host URL format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("API host must start with http:// or https://")
        return v.rstrip("/")

    @computed_field
    @property
    def scheme(self) -> str:
        """Get the URL scheme (http/https)."""
        return urlparse(self.api_host).scheme

    @computed_field
    @property
    def hostname(self) -> str:
        """Get the hostname from the API URL."""
        return urlparse(self.api_host).hostname

    @computed_field
    @property
    def port(self) -> int:
        """Get the port number from the API URL."""
        parsed = urlparse(self.api_host)
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    @property
    def http_url(self) -> str:
        """
        Get the complete HTTP URL for API requests.

        Constructs and caches the HTTP URL based on protocol, host, and port.
        Omits port number for standard ports (80 for HTTP, 443 for HTTPS).

        Returns:
            str: Complete HTTP URL for API requests
        """
        if self._http_url:
            return self._http_url

        default_ports = {"http": 80, "https": 443}
        if self.port in default_ports.values():
            url = f"{self.scheme}://{self.hostname}"
        else:
            url = f"{self.scheme}://{self.hostname}:{self.port}"

        self._http_url = url
        return self._http_url

    @property
    def gql_url(self) -> str:
        """
        Get the complete HTTP URL for GraphQL endpoint.

        Constructs and caches the GraphQL HTTP URL by appending the GraphQL
        API path to the base HTTP URL.

        Returns:
            str: Complete HTTP URL for GraphQL endpoint (/api/graphql)
        """
        if self._gql_url:
            return self._gql_url

        self._gql_url = f"{self.http_url}/api/graphql"
        return self._gql_url

    @property
    def ws_gql_url(self) -> str:
        """
        Get the complete WebSocket URL for GraphQL subscriptions.

        Constructs and caches the WebSocket URL with appropriate protocol
        (ws for HTTP, wss for HTTPS), matching the host and port behavior of
        the HTTP URL, and appends the GraphQL endpoint path.

        Returns:
            str: Complete WebSocket URL for GraphQL subscriptions
        """
        if self._ws_gql_url:
            return self._ws_gql_url

        prefix = "wss" if self.scheme == "https" else "ws"
        websocket_base_url = self.http_url.replace(f"{self.scheme}://", f"{prefix}://", 1)
        self._ws_gql_url = f"{websocket_base_url}/ws/graphql"
        return self._ws_gql_url


try:
    api_settings = ApiSettings()
except Exception as ex:
    print(f"Error loading configuration: {ex}")
    raise
