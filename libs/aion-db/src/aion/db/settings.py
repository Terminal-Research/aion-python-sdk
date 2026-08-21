"""Database configuration settings loaded from environment variables."""

import re
from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DatabaseSettings", "db_settings"]


class DatabaseSettings(BaseSettings):
    """Pydantic settings for PostgreSQL database connectivity.

    Reads configuration from environment variables or a ``.env`` file.
    The primary variable is ``POSTGRES_URL`` which must be a valid
    ``postgresql://`` connection string.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    pg_url: Optional[str] = Field(
        default=None,
        alias="POSTGRES_URL",
        description="Postgres connection URL"
    )

    # Two separate pools sit in front of one PostgreSQL: SQLAlchemy serves
    # ``tasks`` and ADK, raw psycopg serves the LangGraph saver. Both are
    # sized from the same two knobs so the deployer reasons about one
    # connection budget (pods x processes x pools), checked against a given
    # PostgreSQL's ``max_connections``, instead of two pools' worth of
    # library-specific settings.
    pool_min_size: int = Field(
        default=2,
        alias="POSTGRES_POOL_MIN_SIZE",
        description="Minimum size of each PostgreSQL connection pool",
    )
    pool_max_size: int = Field(
        default=10,
        alias="POSTGRES_POOL_MAX_SIZE",
        description="Maximum size of each PostgreSQL connection pool",
    )

    @field_validator('pg_url')
    @classmethod
    def validate_postgres_url(cls, value: Optional[str]) -> Optional[str]:
        """Validate that the URL is a well-formed ``postgresql://`` connection string."""
        if value is None or value == "":
            return None

        if not re.match(r'^postgresql://.*', value):
            raise ValueError(f"Invalid PostgreSQL URL format: {value}")

        try:
            parsed = urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Invalid PostgreSQL URL structure: {value}")
        except Exception as e:
            raise ValueError(f"Failed to parse PostgreSQL URL: {value}") from e

        return value

    def is_valid_pg_url(self) -> bool:
        """Return True if ``pg_url`` is set and has a parseable scheme and host."""
        if not self.pg_url:
            return False
        try:
            parsed = urlparse(self.pg_url)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False

    @property
    def pg_db_name(self) -> Optional[str]:
        """Database name extracted from the connection URL, or None if URL is invalid."""
        if not self.is_valid_pg_url():
            return None
        try:
            parsed = urlparse(self.pg_url)
            return parsed.path.lstrip('/') if parsed.path else None
        except Exception:
            return None

    @property
    def pg_user_name(self) -> Optional[str]:
        """Username extracted from the connection URL, or None if URL is invalid."""
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).username
        except Exception:
            return None

    @property
    def pg_user_password(self) -> Optional[str]:
        """Password extracted from the connection URL, or None if URL is invalid."""
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).password
        except Exception:
            return None

    @property
    def pg_host(self) -> Optional[str]:
        """Hostname extracted from the connection URL, or None if URL is invalid."""
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).hostname
        except Exception:
            return None

    @property
    def pg_port(self) -> Optional[int]:
        """Port number extracted from the connection URL, or None if URL is invalid."""
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).port
        except Exception:
            return None


try:
    db_settings = DatabaseSettings()
except Exception as ex:
    print(f"Error loading database configuration: {ex}")
    raise
