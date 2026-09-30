"""Typed invocation metadata for the Aion Cron A2A extension.

The payload describes a scheduled firing, not the message content or authority
of its sender. Agent implementations decide how to use it in their prompts.
"""

from datetime import datetime, timedelta
from typing import Any

from pydantic import Field, field_validator

from aion.core.a2a import A2ABaseModel

__all__ = ["CronExtensionV1"]


class CronExtensionV1(A2ABaseModel):
    """Cron request metadata collected under the extension's canonical URI.

    ``sent_at`` identifies the producer's dispatch handoff, not receipt,
    execution or completion. The message itself continues to carry content.
    Only UTC timing is exposed; no schedule or timezone lookup is required.
    """

    scheduled_at: datetime = Field(description="Intended UTC firing instant.")
    sent_at: datetime = Field(description="Producer dispatch handoff instant in UTC.")

    @field_validator("scheduled_at", "sent_at", mode="before")
    @classmethod
    def validate_utc_input(cls, value: Any) -> Any:
        """Require UTC datetime objects or the contract's UTC-Z wire strings."""
        if isinstance(value, str) and value.endswith("Z"):
            return value
        if isinstance(value, datetime) and value.utcoffset() == timedelta(0):
            return value
        raise ValueError("Cron timestamps must use UTC (Z)")
