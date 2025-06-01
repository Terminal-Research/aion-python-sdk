"""Utilities for applying database migrations programmatically."""

from __future__ import annotations

from alembic import command

from .env import config

__all__ = ["upgrade_to_head"]


def upgrade_to_head() -> None:
    """Upgrade the database schema to the latest revision.

    This function delegates to :func:`alembic.command.upgrade` using the
    configuration defined in :mod:`aion.server.db.migrations.env`.
    """
    command.upgrade(config, "head")
