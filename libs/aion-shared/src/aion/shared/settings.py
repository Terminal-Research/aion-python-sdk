import re
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "BaseEnvSettings",
    "DatabaseSettings",
    "db_settings",
    "AppSettings",
    "app_settings",
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


class DatabaseSettings(BaseEnvSettings):
    """
    Database configuration settings for PostgreSQL connection.

    This class handles PostgreSQL connection configuration and provides
    convenient properties to extract connection parameters and generate
    different URL formats for various database clients.
    """
    pg_url: Optional[str] = Field(
        default=None,
        alias="POSTGRES_URL",
        description="Postgres connection URL"
    )

    @field_validator('pg_url')
    @classmethod
    def validate_postgres_url(cls, value: Optional[str]) -> Optional[str]:
        """Validate PostgreSQL URL format"""
        if value is None or value == "":
            return None

        # Basic regex for PostgreSQL URL
        postgres_pattern = r'^postgresql://.*'
        if not re.match(postgres_pattern, value):
            raise ValueError(f"Invalid PostgreSQL URL format: {value}")

        # Try to parse with urllib to catch malformed URLs
        try:
            parsed = urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Invalid PostgreSQL URL structure: {value}")
        except Exception as e:
            raise ValueError(f"Failed to parse PostgreSQL URL: {value}") from e

        return value

    def is_valid_pg_url(self) -> bool:
        """Check if PostgreSQL URL is valid and usable"""
        if not self.pg_url:
            return False

        try:
            parsed = urlparse(self.pg_url)
            # Must have scheme and netloc at minimum
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False

    @property
    def pg_sqlalchemy_url(self):
        """Convert PostgreSQL URL to SQLAlchemy-compatible format"""
        from aion.shared.utils.db import sqlalchemy_url
        if not self.is_valid_pg_url():
            return None
        return sqlalchemy_url(self.pg_url)

    @property
    def pg_psycopg_url(self):
        """Convert PostgreSQL URL to psycopg-compatible format"""
        from aion.shared.utils.db import psycopg_url
        if not self.is_valid_pg_url():
            return None
        return psycopg_url(self.pg_url)

    @property
    def pg_db_name(self) -> Optional[str]:
        """Extract database name from PostgreSQL URL"""
        if not self.is_valid_pg_url():
            return None

        try:
            parsed = urlparse(self.pg_url)
            # Remove leading slash from path
            return parsed.path.lstrip('/') if parsed.path else None
        except Exception:
            return None

    @property
    def pg_user_name(self) -> Optional[str]:
        """Extract username from PostgreSQL URL"""
        if not self.is_valid_pg_url():
            return None

        try:
            parsed = urlparse(self.pg_url)
            return parsed.username
        except Exception:
            return None

    @property
    def pg_user_password(self) -> Optional[str]:
        """Extract password from PostgreSQL URL"""
        if not self.is_valid_pg_url():
            return None

        try:
            parsed = urlparse(self.pg_url)
            return parsed.password
        except Exception:
            return None

    @property
    def pg_host(self) -> Optional[str]:
        """Extract host from PostgreSQL URL"""
        if not self.is_valid_pg_url():
            return None

        try:
            parsed = urlparse(self.pg_url)
            return parsed.hostname
        except Exception:
            return None

    @property
    def pg_port(self) -> Optional[int]:
        """Extract port from PostgreSQL URL"""
        if not self.is_valid_pg_url():
            return None

        try:
            parsed = urlparse(self.pg_url)
            return parsed.port
        except Exception:
            return None


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

    # Cached URL properties
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
        WebSocket path to the base HTTP URL.

        Returns:
            str: Complete HTTP URL for GraphQL endpoint (/ws/graphql)
        """
        if self._gql_url:
            return self._gql_url

        self._gql_url = f"{self.http_url}/ws/graphql"
        return self._gql_url

    @property
    def ws_gql_url(self) -> str:
        """
        Get the complete WebSocket URL for GraphQL subscriptions.

        Constructs and caches the WebSocket URL with appropriate protocol
        (ws for HTTP, wss for HTTPS) and GraphQL endpoint path.

        Returns:
            str: Complete WebSocket URL for GraphQL subscriptions
        """
        if self._ws_gql_url:
            return self._ws_gql_url

        prefix = "wss" if self.scheme == "https" else "ws"
        self._ws_gql_url = f"{prefix}://{self.hostname}/ws/graphql"
        return self._ws_gql_url


class AppSettings(BaseEnvSettings):
    """Application configuration settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        description="Logging level to use.",
        alias="LOG_LEVEL",
        default="INFO"
    )

    docs_url: str = Field(
        default="https://docs.aion.to/",
        description="Url to the documentation of Aion API.",
        alias="AION_DOCS_URL"
    )

    node_name: Optional[str] = Field(
        default=None,
        description="Node name used to identify deployment in Aion platform",
        alias="NODE_NAME"
    )

    distribution_id: Optional[str] = Field(
        default=None,
        description="Distribution ID used to identify deployment in Aion platform",
        alias="DISTRIBUTION_ID"
    )

    version_id: Optional[str] = Field(
        default=None,
        description="Version ID used to identify deployment in Aion platform",
        alias="VERSION_ID"
    )

    logstash_host: Optional[str] = Field(
        default=None,
        description="Logstash host to use.",
        alias="LOGSTASH_HOST"
    )

    logstash_port: Optional[int] = Field(
        default=None,
        description="Logstash port to use.",
        alias="LOGSTASH_PORT"
    )

    port: Optional[int] = None

    def set_app_port(self, value: int) -> None:
        self.port = value

    @property
    def is_logstash_configured(self) -> bool:
        """Check if logstash is configured with both host and port."""
        return bool(self.logstash_host and self.logstash_port)


# Initialize settings instances
try:
    db_settings = DatabaseSettings()
    api_settings = ApiSettings()
    app_settings = AppSettings()
except Exception as ex:
    print(f"Error loading configuration: {ex}")
    print("Please check your .env file and ensure all required variables are set.")
    raise
