"""Service for starting AION proxy server process"""
import asyncio

from aion.shared.aion_config import AionConfig
from aion.shared.base import BaseExecuteService
from aion.shared.utils.processes import ProcessManager


class ServeProxyStartupService(BaseExecuteService):
    """
    Service for starting and managing AION proxy server process for the serve command.

    This service handles the initialization and startup of the proxy server,
    creating a separate process for the proxy.
    """

    async def execute(self, config: AionConfig, process_manager: ProcessManager) -> bool:
        """
        Start proxy server in a separate process.

        Args:
            config: AION configuration
            process_manager: ProcessManager instance to create proxy process

        Returns:
            bool: True if proxy started successfully
        """
        # Create and start the proxy process
        success = process_manager.create_process(
            key="proxy", target_function=self._proxy_wrapper, config=config
        )

        if not success:
            self.logger.error("Failed to start proxy server")
        else:
            self.logger.debug("Proxy server started successfully")

        return success

    @staticmethod
    def _proxy_wrapper(config: AionConfig):
        """
        Wrapper function to run proxy server in subprocess.

        Args:
            config: AionConfig instance
        """
        from aion.shared.logging import get_logger

        logger = get_logger()

        try:
            from aion.proxy import AionAgentProxyServer

            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            proxy_server = AionAgentProxyServer(config)

            try:
                # Run the async proxy server function
                loop.run_until_complete(proxy_server.start())
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Proxy server crashed: {str(e)}")
            raise