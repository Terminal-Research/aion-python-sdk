"""Task identifier parsing shared by the durable store and its lease table."""

import uuid

from a2a.utils.errors import InvalidParamsError

__all__ = ["require_task_uuid", "as_uuid"]


def require_task_uuid(task_id: str) -> uuid.UUID:
    """Parse a task identifier, refusing anything the database cannot address.

    Task rows and their leases are keyed by a UUID column, and every read path
    resolves an identifier by parsing it as a UUID. Substituting a generated
    UUID for an unparseable one would write the task under an identifier nobody
    holds: the write reports success, and every subsequent lookup by the
    caller's own identifier returns nothing. Failing loudly keeps that silent
    divergence out of the database.

    A lease and the task it covers must resolve to the same key, which is the
    reason this lives on its own rather than inside either of them.

    Args:
        task_id: Identifier carried by the task being addressed.

    Returns:
        The identifier parsed as a UUID.

    Raises:
        InvalidParamsError: If the identifier is missing or is not a UUID.
    """
    try:
        return uuid.UUID(task_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidParamsError(
            f"Task id must be a UUID for the Postgres task store, got: {task_id!r}"
        ) from exc


def as_uuid(value) -> uuid.UUID:
    """Normalize a database driver's identifier value to a ``UUID``.

    Drivers differ on whether a ``uuid`` column comes back as a ``UUID`` or as
    its text, and raw SQL bypasses the type decorators that would otherwise
    settle it.

    Args:
        value: Identifier as returned by the driver.

    Returns:
        The value as a ``UUID``.
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
