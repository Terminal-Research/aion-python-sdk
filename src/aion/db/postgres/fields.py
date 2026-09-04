"""SQLAlchemy custom column types for Pydantic models and Protobuf messages."""

from typing import Any, Type

from google.protobuf import json_format
from google.protobuf.message import Message as ProtoMessage
from sqlalchemy import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel


class PydanticType(TypeDecorator):
    """JSONB column that deserializes into a Pydantic model or a list of Pydantic models."""

    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_class: Type[BaseModel], *args: Any, many: bool = False, **kwargs: Any):
        """Initialize the column type.

        Args:
            pydantic_class: Pydantic model class used for serialization/deserialization.
            many: If True, the column holds a JSON array and maps to a list of models.
        """
        super().__init__(*args, **kwargs)
        self._pydantic_class = pydantic_class
        self._many = many

    def process_bind_param(self, value, dialect):
        """Serialize a Pydantic model (or list of models) to a JSON-compatible structure before writing to DB."""
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
        """Deserialize a JSONB value from DB into a Pydantic model instance (or list of instances)."""
        if value is None:
            return None

        if self._many:
            return [self._pydantic_class.model_validate(item) for item in value]

        return self._pydantic_class.model_validate(value)


class ProtobufType(TypeDecorator):
    """JSONB column that deserializes into a protobuf Message or a list of protobuf Messages."""

    impl = JSONB
    cache_ok = True

    def __init__(self, proto_class: Type[ProtoMessage], *args: Any, many: bool = False, **kwargs: Any):
        """Initialize the column type.

        Args:
            proto_class: Protobuf message class used for serialization/deserialization.
            many: If True, the column holds a JSON array and maps to a list of messages.
        """
        super().__init__(*args, **kwargs)
        self._proto_class = proto_class
        self._many = many

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Serialize a protobuf message while leaving JSON-like payloads untouched."""
        if isinstance(value, dict):
            return value
        return json_format.MessageToDict(value)

    def process_bind_param(self, value, dialect):
        """Serialize a protobuf message (or list of messages) to a JSON-compatible structure before writing to DB."""
        if value is None:
            return None
        if self._many:
            return [self._serialize_value(item) for item in value]
        return self._serialize_value(value)

    def process_result_value(self, value, dialect):
        """Deserialize a JSONB value from DB into a protobuf message instance (or list of instances)."""
        if value is None:
            return None
        if self._many:
            return [json_format.ParseDict(item, self._proto_class()) for item in value]
        return json_format.ParseDict(value, self._proto_class())
