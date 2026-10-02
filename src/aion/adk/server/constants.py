"""Internal constants for aion.adk.server."""

from aion.db.postgres.constants import ADK_LEGACY_USER_ID as DEFAULT_USER_ID
from aion.db.postgres.constants import ADK_SCHEMA as AION_ADK_SCHEMA

FRAMEWORK = "adk"

__all__ = ["AION_ADK_SCHEMA", "DEFAULT_USER_ID", "FRAMEWORK"]
