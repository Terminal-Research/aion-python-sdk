from typing import Any, Type

from sqlalchemy import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel


class PydanticType(TypeDecorator):
    """JSONB column that deserializes into a Pydantic model or a list of Pydantic models."""

    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_class: Type[BaseModel], *args: Any, many: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._pydantic_class = pydantic_class
        self._many = many

    def process_bind_param(self, value, dialect):
        if value is None:
            return None

        if self._many:
            return [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]

        if isinstance(value, dict):
            return value

        return value.model_dump(mode="json")

    def process_result_value(self, value, dialect):
        if value is None:
            return None

        if self._many:
            return [self._pydantic_class.model_validate(item) for item in value]

        return self._pydantic_class.model_validate(value)
