"""Generic port reservation manager without business logic."""
import socket
from typing import Dict, Optional, Set, Tuple

from .availability import find_free_port_reserved, reserve_port


def serialize_socket(sock: socket.socket) -> tuple:
    """
    Serialize a socket for passing to a subprocess.

    Args:
        sock: Socket to serialize

    Returns:
        tuple: Serialized socket data (reducer function and args)
    """
    from multiprocessing.reduction import _reduce_socket
    # _reduce_socket returns (rebuild_func, (dup_fd, family, type, proto))
    # We only need the args tuple for passing to subprocess
    _, args = _reduce_socket(sock)
    return args


def deserialize_socket(data: tuple) -> socket.socket:
    """
    Deserialize a socket received from parent process.

    Args:
        data: Serialized socket data (dup_fd, family, type, proto)

    Returns:
        socket.socket: Deserialized socket
    """
    from multiprocessing.reduction import _rebuild_socket
    # _rebuild_socket expects (dup_fd, family, type, proto)
    return _rebuild_socket(*data)


class PortReservationManager:
    """
    Generic manager for reserving and releasing ports.

    This class provides a low-level interface for port reservation,
    preventing race conditions where multiple services try to bind to the same port.
    Reserved ports are kept locked via open sockets until explicitly released.

    This is a generic utility without business logic - it doesn't know about
    agents, proxies, or any other domain concepts. Use keys (strings) to
    identify different port reservations.
    """

    def __init__(self):
        """Initialize port reservation manager for localhost."""
        self._reserved: Dict[str, Tuple[int, socket.socket]] = {}
        self._locked_ports: Set[int] = set()
        self._logger = None

    @property
    def logger(self):
        if not self._logger:
            from aion.shared.logging.factory import get_logger
            self._logger = get_logger()
        return self._logger

    def reserve(self, key: str, port: int) -> bool:
        """
        Reserve a specific port.

        Args:
            key: Unique identifier for this reservation
            port: Port number to reserve

        Returns:
            bool: True if reservation successful, False otherwise
        """
        if key in self._reserved:
            self.logger.warning(f"Key '{key}' already has a reserved port")
            return False

        if port in self._locked_ports:
            self.logger.error(f"Port {port} is already reserved")
            return False

        try:
            actual_port, sock = reserve_port(port=port)
            if actual_port != port:
                self.logger.error(f"Reserved port {actual_port} does not match requested port {port}")
                sock.close()
                return False

            self._reserved[key] = (port, sock)
            self._locked_ports.add(port)
            self.logger.debug(f"Reserved port {port} for key '{key}'")
            return True

        except (OSError, socket.error) as e:
            self.logger.error(f"Failed to reserve port {port} for key '{key}': {e}")
            return False

    def reserve_from_range(
            self,
            key: str,
            port_min: int,
            port_max: int
    ) -> Optional[int]:
        """
        Reserve a free port from the specified range.

        Args:
            key: Unique identifier for this reservation
            port_min: Minimum port of the range
            port_max: Maximum port of the range

        Returns:
            Optional[int]: Reserved port number, or None if no port available
        """
        if key in self._reserved:
            self.logger.warning(f"Key '{key}' already has a reserved port")
            return None

        try:
            result = find_free_port_reserved(
                port_min=port_min,
                port_max=port_max,
                excluded_ports=self._locked_ports
            )

            if result is None:
                self.logger.error(
                    f"No free port available in range {port_min}-{port_max} for key '{key}'"
                )
                return None

            port, sock = result
            self._reserved[key] = (port, sock)
            self._locked_ports.add(port)
            self.logger.debug(f"Reserved port {port} for key '{key}' from range {port_min}-{port_max}")
            return port

        except Exception as e:
            self.logger.error(f"Failed to reserve port from range for key '{key}': {e}")
            return None

    def get(self, key: str) -> Optional[int]:
        """
        Get the reserved port number for a key.

        Args:
            key: Reservation identifier

        Returns:
            Optional[int]: Reserved port number, or None if not reserved
        """
        if key in self._reserved:
            return self._reserved[key][0]
        return None

    def get_socket(self, key: str) -> Optional[socket.socket]:
        """
        Get the reserved socket for a key.

        Args:
            key: Reservation identifier

        Returns:
            Optional[socket.socket]: Reserved socket, or None if not reserved
        """
        if key in self._reserved:
            return self._reserved[key][1]
        return None

    def get_serialized_socket(self, key: str) -> Optional[tuple]:
        """
        Get the serialized socket for passing to subprocess.

        Args:
            key: Reservation identifier

        Returns:
            Optional[tuple]: Serialized socket data, or None if not reserved
        """
        sock = self.get_socket(key)
        if sock is None:
            return None
        return serialize_socket(sock)

    def is_port_locked(self, port: int) -> bool:
        """
        Check if a port is currently reserved.

        Args:
            port: Port number to check

        Returns:
            bool: True if port is reserved
        """
        return port in self._locked_ports

    def has_reservation(self, key: str) -> bool:
        """
        Check if a key has a port reservation.

        Args:
            key: Reservation identifier

        Returns:
            bool: True if key has a reservation
        """
        return key in self._reserved

    def release(self, key: str) -> bool:
        """
        Release a reserved port.

        Args:
            key: Reservation identifier

        Returns:
            bool: True if port was released, False if not found
        """
        if key not in self._reserved:
            self.logger.warning(f"No reserved port found for key '{key}'")
            return False

        port, sock = self._reserved[key]

        try:
            sock.close()
            self.logger.debug(f"Released port {port} for key '{key}'")
        except Exception as e:
            self.logger.warning(f"Error closing socket for port {port}: {e}")

        del self._reserved[key]
        self._locked_ports.discard(port)
        return True

    def release_for_binding(self, key: str) -> Optional[int]:
        """
        Release a reserved port so it can be bound by another process.

        This method closes the reservation socket but keeps the port in the locked
        set to prevent other reservations. Use this before starting a server process
        that needs to bind to the port.

        Note: There is a small race condition window between releasing the socket
        and the server binding to the port. This is unavoidable without passing
        the socket file descriptor directly to the subprocess.

        Args:
            key: Reservation identifier

        Returns:
            Optional[int]: Port number that was released, or None if not found
        """
        if key not in self._reserved:
            self.logger.warning(f"No reserved port found for key '{key}'")
            return None

        port, sock = self._reserved[key]

        try:
            sock.close()
            self.logger.debug(f"Released socket for port {port} (key '{key}'), keeping in locked set")
        except Exception as e:
            self.logger.warning(f"Error closing socket for port {port}: {e}")

        # Remove from reserved dict but keep in locked ports
        del self._reserved[key]
        # Port remains in self._locked_ports
        return port

    def release_all(self) -> None:
        """Release all reserved ports."""
        keys = list(self._reserved.keys())
        for key in keys:
            self.release(key)

        self.logger.debug("Released all reserved ports")

    def get_all(self) -> Dict[str, int]:
        """
        Get all reserved ports.

        Returns:
            Dict[str, int]: Mapping of keys to reserved port numbers
        """
        return {key: port for key, (port, _) in self._reserved.items()}

    def count(self) -> int:
        """
        Get the number of reserved ports.

        Returns:
            int: Number of active reservations
        """
        return len(self._reserved)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release all ports."""
        self.release_all()
        return False

    def __del__(self):
        """Destructor - ensure all ports are released."""
        try:
            self.release_all()
        except Exception:
            # Ignore errors during cleanup
            pass
