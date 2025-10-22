"""Service for starting AION proxy server process"""
import asyncio
import os

from aion.proxy import AionAgentProxyServer
from aion.shared.aion_config import AionConfig
from aion.shared.base import BaseExecuteService
from aion.shared.logging import get_logger
from aion.shared.utils.processes import ProcessManager

logger = get_logger()


class ServeProxyStartupService(BaseExecuteService):
    """
    Service for starting and managing AION proxy server process for the serve command.

    This service handles the initialization and startup of the proxy server,
    creating a separate process for the proxy.
    """

    async def execute(self, config: AionConfig, process_manager: ProcessManager) -> bool:
        """
        Start proxy server in a separate process and wait for startup confirmation.

        Args:
            config: AION configuration
            process_manager: ProcessManager instance to create proxy process

        Returns:
            bool: True if proxy started successfully
        """
        # Create and start the proxy process with pipe for communication
        success = process_manager.create_process(
            key="proxy",
            func=self._proxy_wrapper,
            func_kwargs={"config": config},
            use_pipe=True
        )

        if not success:
            self.logger.error("Failed to start proxy server")
            return False

        # Wait for startup confirmation from proxy server
        self.logger.debug("Waiting for proxy server startup confirmation...")
        startup_message = await asyncio.to_thread(
            process_manager.receive_from_process, "proxy", 30.0  # 30 second timeout
        )

        if startup_message and startup_message.get("status") == "started":
            self.logger.debug("Proxy server started successfully")
            return True
        else:
            self.logger.error("Proxy server failed to send startup confirmation")
            return False

    @staticmethod
    def _send_startup_event(conn):
        """
        Send startup confirmation to parent process.

        Args:
            conn: Pipe connection to parent process
        """
        if conn is not None:
            try:
                conn.send({"status": "started", "pid": os.getpid()})
                logger.debug("Sent startup confirmation to parent process")
            except Exception as ex:
                logger.warning(f"Failed to send startup confirmation: {str(ex)}")

    @staticmethod
    def _proxy_wrapper(config: AionConfig, conn=None):
        """
        Wrapper function to run proxy server in subprocess.

        Args:
            config: AionConfig instance
            conn: Pipe connection to parent process (optional)
        """
        try:
            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Create proxy server with startup callback
            proxy_server = AionAgentProxyServer(
                config,
                startup_callback=lambda: ServeProxyStartupService._send_startup_event(conn)
            )

            try:
                # Run the async proxy server function
                loop.run_until_complete(proxy_server.start())
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Proxy server crashed: {str(e)}")
            # Send error status to parent process if connection exists
            if conn is not None:
                try:
                    conn.send({"status": "error", "error": str(e)})
                except Exception:
                    pass
            raise
