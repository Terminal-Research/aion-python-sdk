"""Logging subsystem — custom log record, logger type, and factory."""

from .base import AionLogger, AionLogRecord
from .process import get_process_role, set_process_role

__all__ = ["AionLogger", "AionLogRecord", "get_process_role", "set_process_role"]
