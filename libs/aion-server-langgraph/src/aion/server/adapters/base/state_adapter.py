from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class AgentState:
    values: dict[str, Any] = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)
    is_interrupted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    messages: list[Any] = field(default_factory=list)

    def is_complete(self) -> bool:
        return len(self.next_steps) == 0 and not self.is_interrupted

    def requires_input(self) -> bool:
        return self.is_interrupted

@dataclass
class InterruptInfo:
    reason: str
    prompt: Optional[str] = None
    options: Optional[list[str]] = None
    metadata: dict[str, Any] = field(default_factory=dict)

class StateAdapter(ABC):
    @abstractmethod
    def get_state_from_snapshot(self, snapshot: Any) -> AgentState:
        pass

    @abstractmethod
    def extract_interrupt_info(self, state: AgentState) -> Optional[InterruptInfo]:
        pass

    @abstractmethod
    def create_resume_input(self, user_input: Any, state: AgentState) -> dict[str, Any]:
        pass

    def extract_messages(self, state: AgentState) -> list[Any]:
        return state.messages

    def extract_metadata(self, state: AgentState) -> dict[str, Any]:
        return state.metadata

