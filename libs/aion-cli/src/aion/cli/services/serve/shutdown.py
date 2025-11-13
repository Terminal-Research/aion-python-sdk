"""Service for graceful shutdown of AION serve processes"""
from aion.shared.services import BaseExecuteService
from aion.shared.utils.processes import ProcessManager


class ServeShutdownService(BaseExecuteService):
    """
    Service for gracefully shutting down all AION serve managed processes.

    This service handles the cleanup and graceful termination of all agent
    and proxy processes managed by the ProcessManager.
    """

    async def execute(self, process_manager: ProcessManager, timeout: int = 30) -> bool:
        """
        Gracefully shutdown all processes.

        Args:
            process_manager: ProcessManager instance managing the processes
            timeout: Maximum time in seconds to wait for processes to shutdown

        Returns:
            bool: True if all processes shut down successfully
        """
        self.logger.debug("Shutting down all agents and proxy...")

        if process_manager:
            success = process_manager.shutdown_all(timeout=timeout)
            if success:
                self.logger.debug("All processes shut down successfully")
            else:
                self.logger.warning("Some processes may not have shut down cleanly")
            return success

        return True