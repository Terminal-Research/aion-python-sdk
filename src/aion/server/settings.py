"""Environment-based server configuration settings (log level, Logstash, storage, etc.)."""

from typing import Literal, Optional

from pydantic import Field, field_validator

from aion.core.settings import PLATFORM_SUPPLIED, BaseEnvSettings
from aion.core.utils.optional_deps import server_extras_hint

__all__ = ["AppSettings", "app_settings"]


class AppSettings(BaseEnvSettings):
    """Application configuration settings."""

    file_storage_backend: Optional[Literal["stub", "aion"]] = Field(
        default=None,
        alias="FILE_STORAGE_BACKEND",
        description=(
            "File storage backend for converting inline (base64) file parts to URLs. "
            "When set, inbound and outbound file parts are stored before anything "
            "persists them and replaced with URL references; an inbound file that "
            "cannot be stored rejects the request, an outbound one is dropped and "
            "logged rather than falling back to base64. "
            "Parts the transformer skips, such as JSX Cards, stay inline. "
            "Options: 'aion' (the Aion Files API; requires AION_CLIENT_ID and "
            "AION_CLIENT_SECRET, the server refuses to start without them), "
            "'stub' (development only). Default: None (disabled, base64 passthrough)."
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

    encryption_key: Optional[str] = Field(
        default=None,
        alias="ENCRYPTION_KEY",
        description=(
            "Fernet key used to encrypt sensitive data at rest. It is deliberately "
            "one key for the deployment rather than one per subsystem: every "
            "consumer added later reads this same variable. Today it is applied to "
            "stored push-notification configurations, which carry the callback URL "
            "together with the credentials the receiver expects back — with no key "
            "set those credentials sit in the database as plaintext JSON. Must be a "
            "URL-safe base64-encoded 32-byte key; generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`. Only persistent storage is "
            "affected — in-memory fallbacks persist nothing. Default: unset (data "
            "stored unencrypted)."
        )
    )

    task_ownership_reaper: bool = Field(
        default=True,
        alias="TASK_OWNERSHIP_REAPER",
        description=(
            "Whether this process reclaims task leases whose owner stopped "
            "renewing them. Reclaiming is only safe once every writer "
            "heartbeats, which every deployed instance now does, so it is on "
            "by default; set a falsy value to hold a process back during a "
            "rollout whose older instances do not yet heartbeat. Applies only "
            "where PostgreSQL ownership is in use."
        ),
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

    host_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of the host this process runs on, supplied by the deployment. "
            "It identifies the instance in two places a person looks when "
            "something is wrong: the `host.name` field of every shipped log "
            "line, and the owner reported when a request arrives for a task "
            "another instance is already running. A container runtime's own "
            "HOSTNAME is deliberately not read - it is a random hash under "
            "plain Docker and a developer's machine name locally, and either "
            "would put a plausible but meaningless owner into shared state. "
            "Default: unset, which reads honestly as unknown."
        ),
        alias="HOST_NAME",
        json_schema_extra=PLATFORM_SUPPLIED,
    )

    version_id: Optional[str] = Field(
        default=None,
        description="Version ID used to identify deployment in Aion platform",
        alias="VERSION_ID",
        json_schema_extra=PLATFORM_SUPPLIED,
    )

    logstash_host: Optional[str] = Field(
        default=None,
        description="Logstash host to use.",
        alias="LOGSTASH_HOST",
        json_schema_extra=PLATFORM_SUPPLIED,
    )

    logstash_port: Optional[int] = Field(
        default=None,
        description="Logstash port to use.",
        alias="LOGSTASH_PORT",
        json_schema_extra=PLATFORM_SUPPLIED,
    )

    @field_validator("encryption_key")
    @classmethod
    def validate_encryption_key(cls, value: Optional[str]) -> Optional[str]:
        """Rejects a key Fernet cannot use, at startup rather than at first write.

        The key is otherwise only exercised when a store that uses it is built,
        and a truncated or non-base64 value surfaces there as a bare
        ``ValueError`` raised from inside the A2A SDK, naming neither the
        variable nor the expected format.

        Args:
            value: The configured key, or None/empty when encryption is off.

        Returns:
            The validated key, or None when encryption is off.

        Raises:
            ValueError: If the key is set but unusable, or if the optional
                cryptography dependency it needs is not installed.
        """
        if not value:
            return None

        try:
            from cryptography.fernet import Fernet
        except ImportError as error:
            raise ValueError(
                "ENCRYPTION_KEY is set but the 'cryptography' package that "
                "reads it is not installed.\n" + server_extras_hint()
            ) from error

        try:
            Fernet(value.encode("utf-8"))
        except Exception as error:
            raise ValueError(
                "ENCRYPTION_KEY must be a URL-safe base64-encoded 32-byte key. "
                "Generate one with: python -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\""
            ) from error

        return value

    @property
    def is_logstash_configured(self) -> bool:
        """Return True when both LOGSTASH_HOST and LOGSTASH_PORT are set."""
        return bool(self.logstash_host and self.logstash_port)


try:
    app_settings = AppSettings()
except Exception as ex:
    print(f"Error loading application configuration: {ex}")
    raise
