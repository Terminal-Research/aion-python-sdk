"""Tests for agent message utilities: normalize_role_to_a2a, create_message_from_parts."""

import pytest
from a2a.types import Part, Role

from aion.server.agent.adapters.interfaces.messages import (
    create_message_from_parts,
    normalize_role_to_a2a,
)


def _text_part(text: str = "hello") -> Part:
    return Part(text=text)


class TestNormalizeRoleToA2A:
    def test_user_maps_to_role_user(self):
        """'user' role string maps to Role.ROLE_USER."""
        assert normalize_role_to_a2a("user") == Role.ROLE_USER

    def test_assistant_maps_to_role_agent(self):
        """'assistant' role string maps to Role.ROLE_AGENT."""
        assert normalize_role_to_a2a("assistant") == Role.ROLE_AGENT

    def test_system_maps_to_role_agent(self):
        """'system' role string maps to Role.ROLE_AGENT."""
        assert normalize_role_to_a2a("system") == Role.ROLE_AGENT

    def test_agent_maps_to_role_agent(self):
        """'agent' role string maps to Role.ROLE_AGENT."""
        assert normalize_role_to_a2a("agent") == Role.ROLE_AGENT

    def test_user_is_case_insensitive(self):
        """Role normalization is case-insensitive for the 'user' role."""
        assert normalize_role_to_a2a("USER") == Role.ROLE_USER
        assert normalize_role_to_a2a("User") == Role.ROLE_USER

    def test_unknown_role_falls_back_to_agent(self):
        """Any unrecognized role string falls back to Role.ROLE_AGENT."""
        # Any unrecognized role → agent (non-user = agent)
        assert normalize_role_to_a2a("custom") == Role.ROLE_AGENT


class TestCreateMessageFromParts:
    def test_auto_generates_unique_message_ids(self):
        """create_message_from_parts generates a unique message_id on each call."""
        p = _text_part()
        m1 = create_message_from_parts([p], role="user")
        m2 = create_message_from_parts([p], role="user")
        assert m1.message_id != m2.message_id

    def test_string_role_is_normalized(self):
        """A string role is normalized to the corresponding Role enum value."""
        msg = create_message_from_parts([_text_part()], role="assistant")
        assert msg.role == Role.ROLE_AGENT

    def test_enum_role_used_directly(self):
        """A Role enum value is used directly without normalization."""
        msg = create_message_from_parts([_text_part()], role=Role.ROLE_USER)
        assert msg.role == Role.ROLE_USER

    def test_non_standard_role_stored_in_metadata(self):
        """Roles other than 'user' and 'agent' must be preserved in metadata."""
        msg = create_message_from_parts([_text_part()], role="system")
        assert msg.metadata is not None
        assert "original_role" in msg.metadata
        assert msg.metadata["original_role"] == "system"

    def test_standard_role_not_duplicated_in_metadata(self):
        """Standard roles ('user', 'agent') are not stored in message metadata."""
        msg = create_message_from_parts([_text_part()], role="user")
        # metadata should NOT contain original_role for standard roles
        assert msg.metadata is None or "original_role" not in msg.metadata

    def test_task_and_context_ids_forwarded(self):
        """task_id and context_id are forwarded to the created message."""
        msg = create_message_from_parts(
            [_text_part()],
            role="user",
            task_id="task-1",
            context_id="ctx-1",
        )
        assert msg.task_id == "task-1"
        assert msg.context_id == "ctx-1"

    def test_parts_included_in_message(self):
        """All provided parts are included in the created message."""
        parts = [_text_part("hello"), _text_part("world")]
        msg = create_message_from_parts(parts, role="user")
        assert len(msg.parts) == 2

    def test_caller_metadata_is_merged(self):
        """Caller-provided metadata should be preserved and merged."""
        extra = {"source": "test"}
        msg = create_message_from_parts(
            [_text_part()],
            role="system",
            metadata=extra,
        )
        assert msg.metadata["source"] == "test"
        assert msg.metadata["original_role"] == "system"
