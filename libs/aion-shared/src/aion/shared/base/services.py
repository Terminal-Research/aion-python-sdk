import logging
from abc import ABC, abstractmethod
from typing import Optional, Any

from aion.shared.logging import get_logger


class BaseService(ABC):
    """
    Abstract base class for service implementations.

    This class provides a common interface for all service classes,
    including standardized logging capabilities and execution contract.

    Attributes:
        logger (logging.Logger): Logger instance for the service.

    Example:
        class MyService(BaseService):
            def execute(self) -> str:
                self.logger.info("Executing MyService")
                return "Task completed"
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize the service with optional logger.

        Args:
            logger: Optional logger instance. If None, creates a logger
                   using the module name.
        """
        self.logger = logger or get_logger(self.__class__.__module__)


class BaseExecuteService(BaseService):

    @abstractmethod
    async def execute(self, *args, **kwargs) -> Any:
        """
        Execute the main service logic.

        This method must be implemented by all concrete service classes.

        Returns:
            Any: The result of the service execution.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        pass
