from typing import Optional

from pydantic import Field
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

    @property
    def pg_sqlalchemy_url(self):
        """Convert PostgreSQL URL to SQLAlchemy-compatible format"""
        if not self.pg_url:
            return None

        return sqlalchemy_url(self.pg_url)

    @property
    def pg_psycopg_url(self):
        """Convert PostgreSQL URL to psycopg-compatible format"""
        if not self.pg_url:
            return None

        return psycopg_url(self.pg_url)

    @property
    def pg_db_name(self) -> Optional[str]:
        """Extract database name from PostgreSQL URL"""
        if not self.pg_url:
            return None
        try:
            # postgresql://user:password@host:port/database
            return self.pg_url.split('/')[-1]
        except (IndexError, AttributeError):
            return None

    @property
    def pg_user_name(self) -> Optional[str]:
        """Extract username from PostgreSQL URL"""
        if not self.pg_url:
            return None
        try:
            # postgresql://user:password@host:port/database
            auth_part = self.pg_url.split('://')[1].split('@')[0]
            return auth_part.split(':')[0]
        except (IndexError, AttributeError):
            return None

    @property
    def pg_user_password(self) -> Optional[str]:
        """Extract password from PostgreSQL URL"""
        if not self.pg_url:
            return None
        try:
            # postgresql://user:password@host:port/database
            auth_part = self.pg_url.split('://')[1].split('@')[0]
            return auth_part.split(':')[1]
        except (IndexError, AttributeError):
            return None

    @property
    def pg_host(self) -> Optional[str]:
        """Extract host from PostgreSQL URL"""
        if not self.pg_url:
            return None
        try:
            # postgresql://user:password@host:port/database
            host_part = self.pg_url.split('@')[1].split('/')[0]
            return host_part.split(':')[0]
        except (IndexError, AttributeError):
            return None

    @property
    def pg_port(self) -> Optional[int]:
        """Extract port from PostgreSQL URL"""
        if not self.pg_url:
            return None
        try:
            # postgresql://user:password@host:port/database
            host_part = self.pg_url.split('@')[1].split('/')[0]
            port_str = host_part.split(':')[1]
            return int(port_str)
        except (IndexError, AttributeError, ValueError):
            return None


db_settings = DbSettings()
