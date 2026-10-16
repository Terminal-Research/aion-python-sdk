"""Repository of the version numbers the ADK artifact service gave."""

from __future__ import annotations

from typing import Optional, Type

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.db.postgres.models import AdkArtifactVersionModel
from aion.db.postgres.records import AdkArtifactVersionRecord
from aion.db.postgres.repositories.base import BaseRepository


class AdkArtifactVersionsRepository(BaseRepository[AdkArtifactVersionModel, AdkArtifactVersionRecord]):
    """Repository for ``adk_artifact_versions``, one row per version number given.

    The table is keyed by ``(agent_id, owner_scope, context_id,
    artifact_name, version)`` rather than ``id``, so the base class's
    id-keyed helpers do not apply.
    """

    @property
    def model_class(self) -> Type[AdkArtifactVersionModel]:
        return AdkArtifactVersionModel

    @property
    def entity_class(self) -> Type[AdkArtifactVersionRecord]:
        return AdkArtifactVersionRecord

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
            pg_insert(AdkArtifactVersionModel)
            .values(
                agent_id=agent_id,
                owner_scope=owner_scope,
                context_id=context_id,
                artifact_name=artifact_name,
                version=version,
            )
            .on_conflict_do_nothing()
            .returning(AdkArtifactVersionModel.version)
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
            select(func.max(AdkArtifactVersionModel.version)).where(
                AdkArtifactVersionModel.agent_id == agent_id,
                AdkArtifactVersionModel.owner_scope == owner_scope,
                AdkArtifactVersionModel.context_id == context_id,
                AdkArtifactVersionModel.artifact_name == artifact_name,
            )
        )

    async def delete_context(self, agent_id: str, context_id: str) -> None:
        """Forget every number given in the context, whoever its owner."""
        await self._session.execute(
            delete(AdkArtifactVersionModel).where(
                AdkArtifactVersionModel.agent_id == agent_id,
                AdkArtifactVersionModel.context_id == context_id,
            )
        )
