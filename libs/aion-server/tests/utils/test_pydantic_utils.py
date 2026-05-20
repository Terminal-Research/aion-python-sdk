"""Tests for aion.shared.utils.pydantic — Protobuf and ProtobufEnum annotations.

Focus areas:
  Protobuf[T]:
    - validates a proto message instance directly (pass-through)
    - deserializes a dict into a proto message via ParseDict
    - rejects invalid types with ValueError
    - serializes back to dict via MessageToDict (round-trip)
    - works as optional field (None allowed when wrapped in Optional)
    - works for list fields

  ProtobufEnum[T]:
    - accepts int values
    - accepts proto enum values (converted to int)
    - serializes to int
    - works in Pydantic model context
"""

from typing import List, Optional

import pytest
from a2a.types import Task, TaskState, TaskStatus
from google.protobuf.struct_pb2 import Struct, Value
from pydantic import BaseModel, ValidationError

from aion.server.utils.pydantic import Protobuf, ProtobufEnum


def _make_task(context_id: str = "ctx-1", state: TaskState = TaskState.TASK_STATE_WORKING) -> Task:
    return Task(
        id="task-1",
        context_id=context_id,
        status=TaskStatus(state=state),
    )


class ModelWithProto(BaseModel):
    data: Protobuf[Struct]


class ModelWithOptionalProto(BaseModel):
    data: Optional[Protobuf[Struct]] = None


class ModelWithProtoList(BaseModel):
    items: List[Protobuf[Struct]] = []


class ModelWithTask(BaseModel):
    task: Protobuf[Task]


class TestProtobufAnnotation:
    def test_accepts_proto_instance_directly(self):
        """Verify that accepts proto instance directly."""
        s = Struct()
        m = ModelWithProto(data=s)
        assert m.data is s

    def test_deserializes_dict_to_proto(self):
        """Verify that deserializes dict to proto."""
        m = ModelWithProto(data={"fields": {}})
        assert isinstance(m.data, Struct)

    def test_rejects_invalid_type(self):
        """Verify that rejects invalid type."""
        with pytest.raises((ValidationError, ValueError)):
            ModelWithProto(data="not-a-proto")

    def test_rejects_list_type(self):
        """Verify that rejects list type."""
        with pytest.raises((ValidationError, ValueError)):
            ModelWithProto(data=[1, 2, 3])

    def test_serializes_to_dict(self):
        """Verify that serializes to dict."""
        s = Struct()
        m = ModelWithProto(data=s)
        serialized = m.model_dump()
        assert isinstance(serialized["data"], dict)

    def test_json_round_trip(self):
        """Verify that JSON round trip."""
        s = Struct()
        m = ModelWithProto(data=s)
        json_str = m.model_dump_json()
        restored = ModelWithProto.model_validate_json(json_str)
        assert isinstance(restored.data, Struct)

    def test_optional_field_accepts_none(self):
        """Verify that optional field accepts none."""
        m = ModelWithOptionalProto(data=None)
        assert m.data is None

    def test_optional_field_accepts_proto(self):
        """Verify that optional field accepts proto."""
        s = Struct()
        m = ModelWithOptionalProto(data=s)
        assert isinstance(m.data, Struct)

    def test_list_field_accepts_proto_instances(self):
        """Verify that list field accepts proto instances."""
        s1, s2 = Struct(), Struct()
        m = ModelWithProtoList(items=[s1, s2])
        assert len(m.items) == 2
        assert all(isinstance(x, Struct) for x in m.items)

    def test_list_field_deserializes_dicts(self):
        """Verify that list field deserializes dicts."""
        m = ModelWithProtoList(items=[{"fields": {}}, {"fields": {}}])
        assert all(isinstance(x, Struct) for x in m.items)

    def test_with_task_proto(self):
        """Verify that with task proto."""
        task = _make_task()
        m = ModelWithTask(task=task)
        assert m.task.id == "task-1"

    def test_task_from_dict(self):
        """Verify that task from dict."""
        task_dict = {"id": "t-99", "contextId": "ctx-99", "status": {"state": 1}}
        m = ModelWithTask(task=task_dict)
        assert isinstance(m.task, Task)

    def test_task_serializes_to_dict(self):
        """Verify that task serializes to dict."""
        task = _make_task()
        m = ModelWithTask(task=task)
        dumped = m.model_dump()
        assert isinstance(dumped["task"], dict)


class ModelWithEnum(BaseModel):
    state: ProtobufEnum[TaskState]


class TestProtobufEnumAnnotation:
    def test_accepts_int_directly(self):
        """Verify that accepts int directly."""
        m = ModelWithEnum(state=1)
        assert m.state == 1

    def test_accepts_proto_enum_value(self):
        """Verify that accepts proto enum value."""
        m = ModelWithEnum(state=TaskState.TASK_STATE_WORKING)
        assert m.state == int(TaskState.TASK_STATE_WORKING)

    def test_serializes_to_int(self):
        """Verify that serializes to int."""
        m = ModelWithEnum(state=TaskState.TASK_STATE_COMPLETED)
        dumped = m.model_dump()
        assert isinstance(dumped["state"], int)

    def test_json_serialization_produces_int(self):
        """Verify that JSON serialization produces int."""
        import json
        m = ModelWithEnum(state=TaskState.TASK_STATE_FAILED)
        data = json.loads(m.model_dump_json())
        assert isinstance(data["state"], int)

    def test_zero_value(self):
        """Verify that zero value."""
        m = ModelWithEnum(state=0)
        assert m.state == 0

    def test_all_task_states_accepted(self):
        """Verify that all task states accepted."""
        states = [
            TaskState.TASK_STATE_UNSPECIFIED,
            TaskState.TASK_STATE_SUBMITTED,
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_INPUT_REQUIRED,
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_CANCELED,
        ]
        for s in states:
            m = ModelWithEnum(state=s)
            assert isinstance(m.state, int)
