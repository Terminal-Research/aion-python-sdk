"""Utilities for applying database migrations programmatically."""

from __future__ import annotations

from .migrate import upgrade_to_head

__all__ = ["upgrade_to_head"]
