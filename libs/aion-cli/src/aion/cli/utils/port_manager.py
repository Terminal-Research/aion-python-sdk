"""CLI-specific port manager with business logic for AION agents and proxy."""
from typing import Optional, List

from aion.shared.aion_config import AionConfig
from aion.shared.logging import get_logger
from aion.shared.utils.ports import PortReservationManager

logger = get_logger()


class AionPortManager:
    """
    Port manager with AION-specific business logic for agents and proxy.

    This class wraps the generic PortReservationManager and provides
    domain-specific methods for dynamically reserving ports for AION agents
    and proxy server.

    Example:
        config = AionConfig(...)
        manager = AionPortManager()

        # Reserve all ports dynamically from range
        if manager.reserve_all(config, proxy_port=8080, agent_port_start=8000, agent_port_end=9000):
            # Ports are reserved, start services
            pass

        # Access reserved ports
        agent_port = manager.get_agent_port("agent1")
        proxy_port = manager.get_proxy_port()

        # Release when done
        manager.release_all()
    """

    def __init__(self):
        """Initialize AION port manager for localhost."""
        self._port_manager = PortReservationManager()
        self._config: Optional[AionConfig] = None

    def reserve_all(
        self,
        config: AionConfig,
        proxy_port: Optional[int] = None,
        agent_port_start: int = 8000,
        agent_port_end: int = 9000,
        auto_find_proxy_port: bool = True,
        proxy_port_search_start: int = 8000,
        proxy_port_search_end: int = 8100
    ) -> bool:
        """
        Reserve all required ports for agents and proxy server.

        Ports are allocated dynamically from the specified range.

        Args:
            config: AION configuration
            proxy_port: Optional port for proxy server (if None and auto_find_proxy_port=True, will auto-find)
            agent_port_start: Starting port for agent allocation
            agent_port_end: Ending port for agent allocation
            auto_find_proxy_port: If True and proxy_port is None, auto-find free port
            proxy_port_search_start: Starting port for proxy port search
            proxy_port_search_end: Ending port for proxy port search

        Returns:
            bool: True if all ports reserved successfully, False otherwise
        """
        self._config = config

        # Auto-find or reserve proxy port
        if proxy_port is not None:
            # Explicitly specified port
            if not self._port_manager.reserve("proxy", proxy_port):
                logger.error(f"Failed to reserve proxy port {proxy_port}")
                self._port_manager.release_all()
                return False
            logger.info(f"Reserved proxy port {proxy_port}")
        elif auto_find_proxy_port:
            # Auto-find free port for proxy in specified range
            found_port = self._port_manager.reserve_from_range(
                key="proxy",
                port_min=proxy_port_search_start,
                port_max=proxy_port_search_end
            )
            if found_port is None:
                logger.error(
                    f"Failed to auto-find proxy port in range {proxy_port_search_start}-{proxy_port_search_end}"
                )
                self._port_manager.release_all()
                return False
            logger.info(f"Auto-reserved proxy port {found_port} from range {proxy_port_search_start}-{proxy_port_search_end}")

        # Reserve agent ports dynamically from range
        for agent_id in config.agents.keys():
            port = self._port_manager.reserve_from_range(
                key=agent_id,
                port_min=agent_port_start,
                port_max=agent_port_end
            )
            if port is None:
                logger.error(f"Failed to reserve port for agent '{agent_id}' from range {agent_port_start}-{agent_port_end}")
                self._port_manager.release_all()
                return False
            logger.debug(f"Reserved port {port} for agent '{agent_id}'")

        logger.info(f"Successfully reserved {self._port_manager.count()} ports")
        return True

    def reserve_proxy_from_range(self, port_start: int, port_end: int) -> Optional[int]:
        """
        Reserve a port for proxy server from the specified range.

        Args:
            port_start: Starting port of the range
            port_end: Ending port of the range

        Returns:
            Optional[int]: Reserved port number, or None if no port available
        """
        found_port = self._port_manager.reserve_from_range(
            key="proxy",
            port_min=port_start,
            port_max=port_end
        )
        if found_port:
            logger.info(f"Reserved proxy port {found_port} from range {port_start}-{port_end}")
        return found_port

    def reserve_agent_ports(
        self,
        agent_ids: List[str],
        port_range_start: int,
        port_range_end: int
    ) -> bool:
        """
        Reserve ports for all agents from the specified range.

        Args:
            agent_ids: List of agent IDs
            port_range_start: Starting port of the range
            port_range_end: Ending port of the range

        Returns:
            bool: True if all agent ports reserved successfully
        """
        for agent_id in agent_ids:
            port = self._port_manager.reserve_from_range(
                key=agent_id,
                port_min=port_range_start,
                port_max=port_range_end
            )
            if port is None:
                logger.error(
                    f"Failed to reserve port for agent '{agent_id}' from range {port_range_start}-{port_range_end}"
                )
                return False
            logger.debug(f"Reserved port {port} for agent '{agent_id}'")

        logger.info(f"Successfully reserved {len(agent_ids)} agent ports")
        return True

    def reserve_agent_port(self, agent_id: str, port: int) -> bool:
        """
        Reserve a specific port for an agent.

        Args:
            agent_id: Agent identifier
            port: Port number to reserve

        Returns:
            bool: True if reservation successful
        """
        success = self._port_manager.reserve(agent_id, port)
        if success:
            logger.debug(f"Reserved port {port} for agent '{agent_id}'")
        return success

    def reserve_proxy_port(self, port: int) -> bool:
        """
        Reserve a specific port for the proxy server.

        Args:
            port: Port number to reserve

        Returns:
            bool: True if reservation successful
        """
        success = self._port_manager.reserve("proxy", port)
        if success:
            logger.info(f"Reserved proxy port {port}")
        return success

    def get_agent_port(self, agent_id: str) -> Optional[int]:
        """
        Get the reserved port for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Optional[int]: Reserved port number, or None if not reserved
        """
        return self._port_manager.get(agent_id)

    def get_proxy_port(self) -> Optional[int]:
        """
        Get the reserved port for the proxy server.

        Returns:
            Optional[int]: Reserved port number, or None if not reserved
        """
        return self._port_manager.get("proxy")

    def get_agent_socket_serialized(self, agent_id: str) -> Optional[tuple]:
        """
        Get the serialized socket for an agent (for passing to subprocess).

        Args:
            agent_id: Agent identifier

        Returns:
            Optional[tuple]: Serialized socket data, or None if not reserved
        """
        return self._port_manager.get_serialized_socket(agent_id)

    def get_proxy_socket_serialized(self) -> Optional[tuple]:
        """
        Get the serialized socket for proxy server (for passing to subprocess).

        Returns:
            Optional[tuple]: Serialized socket data, or None if not reserved
        """
        return self._port_manager.get_serialized_socket("proxy")

    def get_all_agent_ports(self) -> dict[str, int]:
        """
        Get all reserved agent ports.

        Returns:
            dict[str, int]: Mapping of agent_id to port number (excluding proxy)
        """
        all_ports = self._port_manager.get_all()
        return {k: v for k, v in all_ports.items() if k != "proxy"}

    def release_agent_port(self, agent_id: str) -> bool:
        """
        Release a reserved port for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            bool: True if port was released
        """
        return self._port_manager.release(agent_id)

    def release_proxy_port(self) -> bool:
        """
        Release the reserved port for the proxy server.

        Returns:
            bool: True if port was released
        """
        return self._port_manager.release("proxy")

    def release_agent_for_binding(self, agent_id: str) -> Optional[int]:
        """
        Release a reserved agent port so it can be bound by the agent server process.

        This closes the reservation socket but keeps tracking the port to prevent
        conflicts with other agents. Call this immediately before starting the agent process.

        Args:
            agent_id: Agent identifier

        Returns:
            Optional[int]: Port number that was released, or None if not found
        """
        return self._port_manager.release_for_binding(agent_id)

    def release_proxy_for_binding(self) -> Optional[int]:
        """
        Release the reserved proxy port so it can be bound by the proxy server process.

        This closes the reservation socket but keeps tracking the port to prevent
        conflicts. Call this immediately before starting the proxy process.

        Returns:
            Optional[int]: Port number that was released, or None if not found
        """
        return self._port_manager.release_for_binding("proxy")

    def release_all(self) -> None:
        """Release all reserved ports."""
        self._port_manager.release_all()
        logger.debug("Released all AION service ports")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release all ports."""
        self.release_all()
        return False
