"""The metadata namespaces the platform reserves for itself.

A2A metadata is an open map, and both the platform and the agent write into
it. Two prefixes belong to the platform:

``aion:``
    Server-owned control flags and typed hints. ``aion:ephemeral`` decides
    whether an event is persisted at all, ``aion:network`` carries routing and
    identity, ``aion:messageType`` decides whether a message enters
    conversation history. A key an agent could set here is a key an agent
    could use to steer the server.

``https://docs.aion.to``
    The extension URIs. An A2A extension addresses its payload by its own URI,
    so the same argument applies to everything under it.

Everything else is the agent's, forwarded to the client untouched.

This module is the one definition of that rule. It lives in ``aion.core``
because every layer needs it and the layers below the server may not import
the server: the deduplicator applies it to inbound events, the framework
adapters apply it where an agent's metadata enters a task, and the authoring
toolkits state it to the agent author.

The typed way to ask for platform behaviour is a parameter -
``emit_message(..., ephemeral=True)``, ``routing=...`` - which the SDK turns
into the ``aion:`` form on the wire. Writing the raw key is what this rule
refuses.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "PLATFORM_METADATA_PREFIX",
    "PLATFORM_METADATA_PREFIXES",
    "is_platform_metadata_key",
    "agent_metadata",
]

PLATFORM_METADATA_PREFIX = "https://docs.aion.to"
"""The documentation origin every Aion A2A extension URI starts with."""

PLATFORM_METADATA_PREFIXES: tuple[str, ...] = (PLATFORM_METADATA_PREFIX, "aion:")
"""Every namespace reserved for the platform. Not open for extension by callers."""


def is_platform_metadata_key(key: Any) -> bool:
    """Return True for a metadata key that belongs to the platform.

    Args:
        key: A metadata key. Anything that is not a string is not a reserved
            key - protobuf map keys are strings, and a non-string here means
            the caller is holding something other than an A2A metadata map.

    Returns:
        True when the key sits in one of the reserved namespaces.
    """
    return isinstance(key, str) and key.startswith(PLATFORM_METADATA_PREFIXES)


def agent_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the part of a metadata map an agent is allowed to write.

    Args:
        metadata: A metadata map, or None.

    Returns:
        A new dict with every reserved key removed. Empty when the input was
        None, empty, or reserved throughout.
    """
    if not metadata:
        return {}
    return {k: v for k, v in metadata.items() if not is_platform_metadata_key(k)}
