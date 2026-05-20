import re
from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DatabaseSettings", "db_settings"]


class DatabaseSettings(BaseSettings):
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

    @field_validator('pg_url')
    @classmethod
    def validate_postgres_url(cls, value: Optional[str]) -> Optional[str]:
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
        if not self.pg_url:
            return False
        try:
            parsed = urlparse(self.pg_url)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False

    @property
    def pg_db_name(self) -> Optional[str]:
        if not self.is_valid_pg_url():
            return None
        try:
            parsed = urlparse(self.pg_url)
            return parsed.path.lstrip('/') if parsed.path else None
        except Exception:
            return None

    @property
    def pg_user_name(self) -> Optional[str]:
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).username
        except Exception:
            return None

    @property
    def pg_user_password(self) -> Optional[str]:
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).password
        except Exception:
            return None

    @property
    def pg_host(self) -> Optional[str]:
        if not self.is_valid_pg_url():
            return None
        try:
            return urlparse(self.pg_url).hostname
        except Exception:
            return None

    @property
    def pg_port(self) -> Optional[int]:
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
