from typing import Optional
from urllib.parse import urlparse
import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from aion.server.db import sqlalchemy_url, psycopg_url


class DbSettings(BaseSettings):
    """
    Database configuration settings for PostgreSQL connection.

    This class handles PostgreSQL connection configuration and provides
    convenient properties to extract connection parameters and generate
    different URL formats for various database clients.
    """
    pg_url: Optional[str] = Field(
        default=None,
        alias="POSTGRES_URL",
        description="Postgres connection URL")

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
        if not self.is_valid_pg_url():
            return None
        return sqlalchemy_url(self.pg_url)

    @property
    def pg_psycopg_url(self):
        """Convert PostgreSQL URL to psycopg-compatible format"""
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


db_settings = DbSettings()
