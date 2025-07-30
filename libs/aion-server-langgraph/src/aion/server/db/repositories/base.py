"""Base repository implementation."""

from abc import ABC, abstractmethod
from typing import Type, TypeVar, Generic, Optional, List
import uuid

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

    @abstractmethod
    async def save(self, entity: EntityT) -> EntityT:
        """Save or update an entity."""
        raise NotImplementedError

    @abstractmethod
    async def find_by_id(self, id: uuid.UUID) -> Optional[EntityT]:
        """Find entity by ID."""
        raise NotImplementedError

    @abstractmethod
    async def delete_by_id(self, id: uuid.UUID) -> bool:
        """Delete entity by ID."""
        raise NotImplementedError
