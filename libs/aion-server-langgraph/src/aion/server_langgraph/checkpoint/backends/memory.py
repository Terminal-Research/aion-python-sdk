"""In-memory checkpointer backend."""

from langgraph.checkpoint.memory import InMemorySaver

from .base import CheckpointerBackend


class MemoryBackend(CheckpointerBackend):
    """In-memory checkpointer backend.

    Always available. Checkpoints are lost on restart.
    Use for development, testing, or when a database is unavailable.
    """

    def is_available(self) -> bool:
        return True

    async def create(self) -> InMemorySaver:
        return InMemorySaver()


__all__ = ["MemoryBackend"]
