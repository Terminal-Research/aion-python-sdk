"""Common types for database queries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SortKey:
    """A single sort criterion."""

    column: str
    """Name of the model attribute (column) to sort by."""
    descending: bool = True
    """If True, sort in descending order; otherwise ascending."""


class Sorting:
    """Ordered collection of sort keys applied to a query."""

    def __init__(self, *keys: SortKey):
        """Initialize with one or more sort keys applied in the given order.

        Args:
            keys: Sort criteria applied left-to-right as ORDER BY clauses.
        """
        self.keys = list(keys)


@dataclass
class Pagination:
    """Limit/offset pagination parameters."""

    limit: int | None = None
    """Maximum number of rows to return. None means no limit."""
    offset: int | None = None
    """Number of rows to skip before returning results. None means no offset."""
