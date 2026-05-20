from typing import Annotated, Any

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtoMessage
from pydantic_core import core_schema

from pydantic import GetCoreSchemaHandler

__all__ = ["Protobuf", "ProtobufEnum"]


class _ProtobufAnnotation:
    def __init__(self, proto_cls: type) -> None:
        self._proto_cls = proto_cls

    def __get_pydantic_core_schema__(
            self,
            source_type: type,
            handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        proto_cls = self._proto_cls

        def validate(v: object) -> ProtoMessage:
            if isinstance(v, proto_cls):
                return v
            if isinstance(v, dict):
                return ParseDict(v, proto_cls())
            raise ValueError(f"Expected {proto_cls.__name__} or dict, got {type(v).__name__}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                MessageToDict,
            ),
        )


class Protobuf:
    """Pydantic annotation for protobuf message fields.

    Handles automatic serialization (protobuf -> dict) and deserialization
    (dict -> protobuf) for fields containing protobuf Message objects.

    Usage:
        class MyModel(BaseModel):
            message: Protobuf[Message]
            history: list[Protobuf[Message]] = Field(default_factory=list)
    """

    def __class_getitem__(cls, proto_cls: type) -> Any:
        return Annotated[proto_cls, _ProtobufAnnotation(proto_cls)]


class _ProtobufEnumAnnotation:
    @staticmethod
    def __get_pydantic_core_schema__(
            source_type: type,
            handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            lambda v: int(v),
            serialization=core_schema.plain_serializer_function_ser_schema(int),
        )


class ProtobufEnum:
    """Pydantic annotation for protobuf enum fields.

    Protobuf enums use EnumTypeWrapper as metaclass which Pydantic cannot
    validate. This annotation tells Pydantic to treat the value as int
    while preserving the original type in the annotation for readability.

    Usage:
        class MyModel(BaseModel):
            state: ProtobufEnum[TaskState]
    """

    def __class_getitem__(cls, enum_cls: type) -> Any:
        return Annotated[enum_cls, _ProtobufEnumAnnotation()]
