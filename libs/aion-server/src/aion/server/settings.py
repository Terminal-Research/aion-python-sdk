"""Environment-based server configuration settings (log level, Logstash, storage, etc.)."""

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

    push_notification_timeout_seconds: float = Field(
        default=30.0,
        alias="PUSH_NOTIFICATION_TIMEOUT_SECONDS",
        description=(
            "Read/write timeout for webhook deliveries, in seconds. The httpx "
            "default of 5s is too short for a receiver that does real work on the "
            "callback before answering, which shows up as httpx.ReadTimeout even "
            "though the request was accepted. Connect timeout stays at 5s: an "
            "unreachable host should fail fast. Note that the first delivery of a "
            "run is awaited in the request path, so this value bounds how long a "
            "slow webhook can delay the message/send response."
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
        """Return True when both LOGSTASH_HOST and LOGSTASH_PORT are set."""
        return bool(self.logstash_host and self.logstash_port)


try:
    app_settings = AppSettings()
except Exception as ex:
    print(f"Error loading application configuration: {ex}")
    raise
