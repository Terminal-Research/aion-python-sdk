"""Common types for database queries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SortKey:
    """A single sort criterion."""

    column: str
    descending: bool = True


class Sorting:
    """Ordered collection of sort keys applied to a query."""

    def __init__(self, *keys: SortKey):
        self.keys = list(keys)


@dataclass
class Pagination:
    """Limit/offset pagination parameters."""

    limit: int | None = None
    offset: int | None = None
