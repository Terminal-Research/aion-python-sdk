"""In-memory checkpointer backend."""

from langgraph.checkpoint.memory import InMemorySaver

from ..files import LinkedFilesSaver
from .base import CheckpointerBackend


class AionInMemorySaver(LinkedFilesSaver, InMemorySaver):
    """InMemorySaver that keeps a file a message of the state carries inline as a link to the stored file."""


class MemoryBackend(CheckpointerBackend):
    """In-memory checkpointer backend.

    Always available. Checkpoints are lost on restart.
    Use for development, testing, or when a database is unavailable.
    """

    def is_available(self) -> bool:
        return True

    async def create(self) -> AionInMemorySaver:
        return AionInMemorySaver()


__all__ = ["AionInMemorySaver", "MemoryBackend"]
