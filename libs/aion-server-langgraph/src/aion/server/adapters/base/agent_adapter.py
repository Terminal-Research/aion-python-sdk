from abc import ABC, abstractmethod
from typing import Any, Optional

from aion.shared.aion_config.models import AgentConfig

from aion.server.adapters.base.executor_adapter import ExecutorAdapter

class AgentAdapter(ABC):
    @property
    @abstractmethod
    def framework_name(self) -> str:
        pass

    @abstractmethod
    def can_handle(self, agent_obj: Any) -> bool:
        pass

    @abstractmethod
    def discover_agent(self, module: Any, config: AgentConfig) -> Any:
        pass

    @abstractmethod
    def initialize_agent(self, agent_obj: Any, config: AgentConfig) -> Any:
        pass

    @abstractmethod
    def create_executor(self, agent: Any, config: AgentConfig) -> ExecutorAdapter:
        pass

    @abstractmethod
    def validate_config(self, config: AgentConfig) -> None:
        pass

    def get_metadata(self, agent: Any) -> dict[str, Any]:
        return {"framework": self.framework_name}

