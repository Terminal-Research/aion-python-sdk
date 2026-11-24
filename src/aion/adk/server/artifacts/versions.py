"""The version numbers the ADK artifact service gave, once each across the servers of an agent.

The artifact service numbers the versions of an artifact per agent, owner,
context and name - ADK's ``app_name``, ``user_id``, ``session_id`` and
``filename`` - and reads them back from the task tables, which an artifact
reaches only later, with the task that carries it. Each number is recorded
here when it is given, under that key as the primary key, so two servers
saving the same artifact at once cannot give one number: the second insert
conflicts, and its server numbers again from what the database holds.

The table lies in the ADK schema with the SDK's other ADK tables, from their
migrations (``aion.adk.server.tables``).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, DateTime, Integer, MetaData, PrimaryKeyConstraint, Table, Text, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aion.adk.server.constants import AION_ADK_SCHEMA

__all__ = ["ARTIFACT_VERSIONS_TABLE", "ArtifactVersions", "artifact_versions"]

ARTIFACT_VERSIONS_TABLE = "adk_artifact_versions"

artifact_versions = Table(
    ARTIFACT_VERSIONS_TABLE,
    MetaData(schema=AION_ADK_SCHEMA),
    Column("agent_id", Text, nullable=False),
    Column("owner_scope", Text, nullable=False),
    Column("context_id", Text, nullable=False),
    Column("artifact_name", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()),
    PrimaryKeyConstraint("agent_id", "owner_scope", "context_id", "artifact_name", "version"),
)
"""One row per version number given: agent (``app_name``), owner (``user_id``), context (``session_id``), name, version."""


class ArtifactVersions:
    """The version numbers given, read and recorded through one database session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
            self,
            *,
            agent_id: str,
            owner_scope: str,
            context_id: str,
            artifact_name: str,
            version: int,
    ) -> bool:
        """Record ``version`` as given, unless it already is; whether this call recorded it.

        ``INSERT ... ON CONFLICT DO NOTHING`` waits for a concurrent insert of
        the same number to commit or roll back, so of two servers giving one
        number exactly one records it. Commit the session for the number to
        hold.
        """
        inserted = await self._session.execute(
            pg_insert(artifact_versions)
            .values(
                agent_id=agent_id,
                owner_scope=owner_scope,
                context_id=context_id,
                artifact_name=artifact_name,
                version=version,
            )
            .on_conflict_do_nothing()
            .returning(artifact_versions.c.version)
        )
        return inserted.first() is not None

    async def latest(
            self,
            *,
            agent_id: str,
            owner_scope: str,
            context_id: str,
            artifact_name: str,
    ) -> Optional[int]:
        """The highest version number given to the artifact, or None when none was."""
        return await self._session.scalar(
            select(func.max(artifact_versions.c.version)).where(
                artifact_versions.c.agent_id == agent_id,
                artifact_versions.c.owner_scope == owner_scope,
                artifact_versions.c.context_id == context_id,
                artifact_versions.c.artifact_name == artifact_name,
            )
        )

    async def delete_context(self, agent_id: str, context_id: str) -> None:
        """Forget every number given in the context, whoever its owner."""
        await self._session.execute(
            delete(artifact_versions).where(
                artifact_versions.c.agent_id == agent_id,
                artifact_versions.c.context_id == context_id,
            )
        )
