"""Service for starting AION proxy server process"""
import asyncio
import os
from typing import Dict

from aion.proxy import AionAgentProxyServer
from aion.shared.config import AionConfig
from aion.shared.services import BaseExecuteService
from aion.shared.logging import get_logger
from aion.shared.utils.processes import ProcessManager

logger = get_logger()


class ServeProxyStartupService(BaseExecuteService):
    """
    Service for starting and managing AION proxy server process for the serve command.

    This service handles the initialization and startup of the proxy server,
    creating a separate process for the proxy.
    """

    async def execute(
        self,
        port: int,
        agents: Dict[str, str],
        process_manager: ProcessManager,
        port_manager=None,
        startup_timeout: int = 30
    ) -> bool:
        """
        Start proxy server in a separate process and wait for startup confirmation.

        Args:
            port: Port number for proxy server
            agents: Dictionary mapping agent_id to agent_url (e.g., {"my-agent": "http://0.0.0.0:8001"})
            process_manager: ProcessManager instance to create proxy process
            port_manager: Optional AionPortManager instance to release port reservation
            startup_timeout: Timeout in seconds for startup confirmation (0 to skip)

        Returns:
            bool: True if proxy started successfully
        """
        # Get serialized socket for passing to subprocess
        serialized_socket = None
        if port_manager is not None:
            serialized_socket = port_manager.get_proxy_socket_serialized()
            if serialized_socket is None:
                self.logger.error("Failed to get socket for proxy")
                return False
            self.logger.debug(f"Passing socket for port {port} to proxy")

        # Create and start the proxy process with pipe for communication
        success = process_manager.create_process(
            key="proxy",
            func=self._proxy_wrapper,
            func_kwargs={
                "port": port,
                "agents": agents,
                "serialized_socket": serialized_socket,
            },
            use_pipe=True
        )

        if not success:
            self.logger.error("Failed to start proxy server")
            return False

        # Skip confirmation check if timeout is 0
        if startup_timeout == 0:
            self.logger.debug("Proxy server process started (startup confirmation skipped)")
            return True

        # Wait for startup confirmation from proxy server
        self.logger.debug(f"Waiting for proxy server startup confirmation (timeout: {startup_timeout}s)...")
        startup_message = await asyncio.to_thread(
            process_manager.receive_from_process, "proxy", float(startup_timeout)
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
            except Exception as ex:
                logger.warning(f"Failed to send startup confirmation: {str(ex)}")

    @staticmethod
    def _proxy_wrapper(port: int, agents: Dict[str, str], serialized_socket=None, conn=None):
        """
        Wrapper function to run proxy server in subprocess.

        Args:
            port: Port number for proxy server
            agents: Dictionary mapping agent_id to agent_url
            serialized_socket: Serialized socket from parent process (optional)
            conn: Pipe connection to parent process (optional)
        """
        try:
            # Reset signal handlers to default in child process
            # This prevents inherited signal handlers from parent process from running
            # which would cause issues with process management (AssertionError in is_alive)
            import signal
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

            # Create new event loop for this process
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Create proxy server with startup callback
            proxy_server = AionAgentProxyServer(
                agents=agents,
                startup_callback=lambda: ServeProxyStartupService._send_startup_event(conn)
            )

            try:
                # Run the async proxy server function with socket
                loop.run_until_complete(
                    proxy_server.start(port=port, serialized_socket=serialized_socket)
                )
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
