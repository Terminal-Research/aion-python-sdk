from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Optional

from aion.server.adapters.base.state_adapter import AgentState

class ExecutionConfig:
    def __init__(
        self,
        session_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.session_id = session_id
        self.thread_id = thread_id
        self.timeout = timeout
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "timeout": self.timeout,
            "metadata": self.metadata,
        }

class ExecutionEvent:
    def __init__(
        self,
        event_type: str,
        data: Any,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.event_type = event_type
        self.data = data
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return f"ExecutionEvent(type={self.event_type}, data={self.data})"

class ExecutorAdapter(ABC):
    @abstractmethod
    async def invoke(
        self,
        inputs: dict[str, Any],
        config: Optional[ExecutionConfig] = None,
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    async def stream(
        self,
        inputs: dict[str, Any],
        config: Optional[ExecutionConfig] = None,
    ) -> AsyncIterator[ExecutionEvent]:
        pass

    @abstractmethod
    async def get_state(self, config: ExecutionConfig) -> AgentState:
        pass

    @abstractmethod
    async def resume(
        self,
        inputs: Optional[dict[str, Any]],
        config: ExecutionConfig,
    ) -> AsyncIterator[ExecutionEvent]:
        pass

    def supports_streaming(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    def supports_state_retrieval(self) -> bool:
        return True

