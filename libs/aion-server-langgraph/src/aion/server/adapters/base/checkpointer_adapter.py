from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

class CheckpointerType(str, Enum):
    MEMORY = "memory"
    POSTGRES = "postgres"
    REDIS = "redis"
    SQLITE = "sqlite"
    FILE = "file"

@dataclass
class CheckpointerConfig:
    type: CheckpointerType
    connection_string: Optional[str] = None
    namespace: Optional[str] = None
    ttl: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class Checkpoint:
    id: str
    thread_id: str
    state: dict[str, Any]
    timestamp: float
    parent_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

class CheckpointerAdapter(ABC):
    @abstractmethod
    async def create_checkpointer(self, config: CheckpointerConfig) -> Any:
        pass

    @abstractmethod
    async def save_checkpoint(self, checkpoint: Checkpoint, checkpointer: Any) -> None:
        pass

    @abstractmethod
    async def load_checkpoint(
        self, thread_id: str, checkpointer: Any, checkpoint_id: Optional[str] = None
    ) -> Optional[Checkpoint]:
        pass

    @abstractmethod
    async def list_checkpoints(
        self, thread_id: str, checkpointer: Any, limit: Optional[int] = None
    ) -> list[Checkpoint]:
        pass

    @abstractmethod
    async def delete_checkpoint(self, checkpoint_id: str, checkpointer: Any) -> bool:
        pass

    @abstractmethod
    async def cleanup_expired(self, checkpointer: Any) -> int:
        pass

    def supports_history(self) -> bool:
        return True

    def supports_ttl(self) -> bool:
        return True

    def supports_namespace(self) -> bool:
        return True

    async def validate_connection(self, checkpointer: Any) -> bool:
        return True

