from typing import Literal, Optional

from pydantic import Field

from aion.core.settings import BaseEnvSettings

__all__ = ["AppSettings", "app_settings"]


class AppSettings(BaseEnvSettings):
    """Application configuration settings."""

    file_storage_backend: Optional[Literal["stub"]] = Field(
        default=None,
        alias="FILE_STORAGE_BACKEND",
        description=(
            "File storage backend for converting inline (base64) file parts to URLs. "
            "When set, outgoing A2A events with binary content are uploaded to storage "
            "and replaced with URL references, minimizing content stored in tables. "
            "Options: 'stub' (development only). Default: None (disabled, base64 passthrough)."
        )
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        description="Logging level to use.",
        alias="LOG_LEVEL",
        default="INFO"
    )

    docs_url: str = Field(
        default="https://docs.aion.to",
        description="Url to the documentation of Aion API.",
        alias="AION_DOCS_URL"
    )

    node_name: Optional[str] = Field(
        default=None,
        description="Node name used to identify deployment in Aion platform",
        alias="NODE_NAME"
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

    @property
    def is_logstash_configured(self) -> bool:
        return bool(self.logstash_host and self.logstash_port)


try:
    app_settings = AppSettings()
except Exception as ex:
    print(f"Error loading application configuration: {ex}")
    raise
