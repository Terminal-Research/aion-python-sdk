"""Typed invocation metadata for the Aion Cron A2A extension.

The payload describes a scheduled firing, not the message content or authority
of its sender. Agent implementations decide how to use it in their prompts.
"""

from datetime import datetime, timedelta
from typing import Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from aion.core.a2a import A2ABaseModel

__all__ = ["CronScheduleV1", "CronExtensionV1"]


class CronScheduleV1(A2ABaseModel):
    """Accepted schedule snapshot that produced one occurrence.

    The schedule ID is local to its attachment; the timezone is independent
    of the UTC occurrence timestamps. Only recurring schedules have an
    expression. This model does not implement scheduling or change cadence.
    """

    id: UUID = Field(description="Schedule-local ID, qualified by attachmentId.")
    type: Literal["recurring", "one-time"] = Field(description="Schedule kind.")
    timezone: str = Field(description="Named timezone of the accepted schedule.")
    description: str = Field(description="Human-readable schedule description.")
    cron_expression: str | None = Field(
        default=None,
        description="Five-field UNIX cron expression; recurring schedules only.",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Validate the named zone, matching control-plane normalization."""
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Invalid cron timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        """Reject incomplete or contradictory discriminator-dependent fields."""
        if self.type == "recurring":
            expression_valid = (
                self.cron_expression is not None
                and len(self.cron_expression.split()) == 5
            )
        else:
            expression_valid = self.cron_expression is None
        if not expression_valid or not self.description.strip():
            raise ValueError(
                "Cron schedule requires a description and a five-field expression "
                "only when recurring"
            )
        return self


class CronExtensionV1(A2ABaseModel):
    """Cron request metadata collected under the extension's canonical URI.

    ``sent_at`` identifies the producer's dispatch handoff, not receipt,
    execution or completion. The message itself continues to carry content.
    """

    attachment_id: UUID = Field(description="Stable attachment Recording ID.")
    occurrence_id: UUID = Field(description="Durable ID of this scheduled firing.")
    schedule: CronScheduleV1 = Field(description="Accepted schedule snapshot.")
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
