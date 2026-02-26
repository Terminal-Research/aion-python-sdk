"""Base repository implementation."""

from abc import ABC, abstractmethod
from typing import Type, TypeVar, Generic, Optional, List
import uuid

from aion.db.postgres.types import Sorting, Pagination

from sqlalchemy import Select, select, delete, exists, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar('ModelT')
EntityT = TypeVar('EntityT')


class BaseRepository(ABC, Generic[ModelT, EntityT]):
    """Base repository with entity-model separation."""

    def __init__(self, session: AsyncSession):
        self._session = session

    @property
    @abstractmethod
    def model_class(self) -> Type[ModelT]:
        """SQLAlchemy model class."""
        raise NotImplementedError

    @property
    @abstractmethod
    def entity_class(self) -> Type[EntityT]:
        """Domain entity class."""
        raise NotImplementedError

    async def find_by_id(self, id: uuid.UUID) -> Optional[EntityT]:
        """Find row by ID."""
        stmt = select(self.model_class).where(self.model_class.id == id)
        return await self._execute_and_convert(stmt)

    async def delete_by_id(self, id: uuid.UUID) -> bool:
        """Delete row by ID."""
        stmt = delete(self.model_class).where(self.model_class.id == id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def exists_by_id(self, id: uuid.UUID) -> bool:
        """Check if task exists."""
        stmt = select(exists().where(self.model_class.id == id))
        result = await self._session.execute(stmt)
        return result.scalar()

    @staticmethod
    def _apply_pagination(stmt: Select, pagination: Pagination) -> Select:
        if pagination.offset:
            stmt = stmt.offset(pagination.offset)
        if pagination.limit:
            stmt = stmt.limit(pagination.limit)
        return stmt

    def _apply_sorting(self, stmt: Select, sorting: Sorting) -> Select:
        for key in sorting.keys:
            col = getattr(self.model_class, key.column)
            stmt = stmt.order_by(desc(col) if key.descending else asc(col))
        return stmt

    async def _execute_and_convert_many(self, stmt: Select) -> List[EntityT]:
        """Execute query and return list of entities."""
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self.entity_class.model_validate(model, from_attributes=True) for model in models]

    async def _execute_and_convert(self, stmt: Select) -> Optional[EntityT]:
        """Execute query and return single entity or None."""
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        if not model:
            return None

        return self.entity_class.model_validate(model, from_attributes=True)
