"""TaskOwnershipBusy's JSON-RPC identity: a dedicated code, not a fallback.

See aion.server.tasks.ownership.types for why this is a module-level patch
into a2a-sdk's own error maps rather than something owned by aion.
"""

from __future__ import annotations

import pytest
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.server.request_handlers.response_helpers import (
    EXCEPTION_MAP,
    build_error_response,
)
from a2a.utils.errors import (
    A2A_ERROR_MAPPING,
    A2A_ERROR_REASONS,
    A2A_REASON_TO_ERROR,
    JSON_RPC_ERROR_CODE_MAP,
    A2AError,
)

from aion.server.core.errors import AION_ERROR_CODE_RANGE, register_aion_error
from aion.server.tasks.ownership.types import (
    TASK_OWNERSHIP_BUSY_CODE,
    TaskOwnershipBusy,
)


def test_task_ownership_busy_has_its_own_json_rpc_code() -> None:
    assert JSON_RPC_ERROR_CODE_MAP[TaskOwnershipBusy] == TASK_OWNERSHIP_BUSY_CODE
    # Distinct from InternalError's -32603: that is the fallback an unmapped
    # A2AError gets, and the whole point of registering a code here is to not
    # be that fallback.
    assert TASK_OWNERSHIP_BUSY_CODE != -32603
    assert TASK_OWNERSHIP_BUSY_CODE in AION_ERROR_CODE_RANGE


def test_no_two_registered_errors_share_a_json_rpc_code() -> None:
    codes = list(JSON_RPC_ERROR_CODE_MAP.values())
    assert len(codes) == len(set(codes))


def test_register_aion_error_refuses_a_code_outside_the_reserved_range() -> None:
    class _OutOfRangeError(A2AError):
        message = "out of range"

    with pytest.raises(ValueError, match="outside Aion's reserved range"):
        register_aion_error(
            _OutOfRangeError,
            -32010,
            http_status=409,
            grpc_status="ABORTED",
            reason="OUT_OF_RANGE",
        )


def test_register_aion_error_refuses_a_code_already_claimed() -> None:
    class _CollidingError(A2AError):
        message = "collides with TaskOwnershipBusy's code"

    with pytest.raises(ValueError, match="already claimed"):
        register_aion_error(
            _CollidingError,
            TASK_OWNERSHIP_BUSY_CODE,
            http_status=409,
            grpc_status="ABORTED",
            reason="COLLIDING_ERROR",
        )


def test_register_aion_error_refuses_a_reason_already_claimed() -> None:
    class _CollidingReasonError(A2AError):
        message = "collides with TaskOwnershipBusy's reason"

    with pytest.raises(ValueError, match="Reason"):
        register_aion_error(
            _CollidingReasonError,
            TASK_OWNERSHIP_BUSY_CODE - 1,
            http_status=409,
            grpc_status="ABORTED",
            reason="TASK_OWNERSHIP_BUSY",
        )


def test_task_ownership_busy_is_reported_as_a_plain_jsonrpc_error() -> None:
    # Not JSONRPCInternalError - a busy task is not an internal failure.
    assert EXCEPTION_MAP[TaskOwnershipBusy] is JSONRPCError


def test_task_ownership_busy_has_a_rest_grpc_mapping_and_reason_round_trip() -> None:
    mapping = A2A_ERROR_MAPPING[TaskOwnershipBusy]
    assert A2A_ERROR_REASONS[TaskOwnershipBusy] == mapping.reason
    assert A2A_REASON_TO_ERROR[mapping.reason] is TaskOwnershipBusy


def test_the_built_response_carries_the_dedicated_code_and_no_retryable_flag() -> None:
    error = TaskOwnershipBusy("task-1", owner_instance_id="pod-a")

    response = build_error_response("req-1", error)

    assert response["error"]["code"] == TASK_OWNERSHIP_BUSY_CODE
    error_info = response["error"]["data"][0]
    assert error_info["reason"] == "TASK_OWNERSHIP_BUSY"
    metadata = error_info["metadata"]
    assert metadata["task_id"] == "task-1"
    assert metadata["owner_instance_id"] == "pod-a"
    assert "retryable" not in metadata
