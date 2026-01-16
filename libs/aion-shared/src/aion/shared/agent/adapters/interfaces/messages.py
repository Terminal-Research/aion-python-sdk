"""Message utilities for agent communication.

This module provides utilities for working with a2a.Message in agent adapters.
All message types now use a2a protocol types directly.

Import a2a types directly from a2a.types:
- from a2a.types import Message, Role, Part

Utilities:
- normalize_role_to_a2a: Convert framework roles (system, assistant) to a2a Role
- create_message_from_parts: Helper to create a2a.Message with proper defaults
"""

from typing import Any
from uuid import uuid4

from a2a.types import Message, Role, Part

__all__ = [
    "normalize_role_to_a2a",
    "create_message_from_parts",
]


def normalize_role_to_a2a(role: str) -> Role:
    """Normalize framework-specific roles to a2a Role enum.

    Maps:
    - "user" → Role.user
    - "assistant", "system", "agent" → Role.agent

    Args:
        role: Framework-specific role string

    Returns:
        a2a Role enum value
    """
    if role.lower() == "user":
        return Role.user
    else:
        # assistant, system, agent all map to agent
        return Role.agent


def create_message_from_parts(
    parts: list[Part],
    role: str | Role,
    task_id: str | None = None,
    context_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Message:
    """Create an a2a.Message with proper defaults.

    Helper to create a2a.Message with:
    - Auto-generated message_id (UUID)
    - Role normalization
    - Optional metadata for original role

    Args:
        parts: List of a2a Part objects
        role: Role string or a2a.Role enum
        task_id: Optional task ID
        context_id: Optional context ID
        metadata: Optional additional metadata

    Returns:
        a2a.Message instance
    """
    # Normalize role
    if isinstance(role, str):
        original_role = role
        a2a_role = normalize_role_to_a2a(role)

        # Store original role in metadata if it differs
        if metadata is None:
            metadata = {}
        if original_role not in ("user", "agent"):
            metadata["original_role"] = original_role
    else:
        a2a_role = role

    return Message(
        message_id=str(uuid4()),
        role=a2a_role,
        parts=parts,
        task_id=task_id,
        context_id=context_id,
        metadata=metadata,
    )
