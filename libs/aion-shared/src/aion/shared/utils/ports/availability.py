import socket
from typing import Optional, Set, Callable, Tuple


def is_port_available(port: int) -> bool:
    """
    Check if a port is available for binding on localhost.

    Args:
        port: Port number to check

    Returns:
        bool: True if port is available, False otherwise
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            return True
    except (OSError, socket.error):
        return False


def reserve_port(port: int = 0) -> Tuple[int, socket.socket]:
    """
    Reserve a port by creating and binding a socket on localhost.

    This prevents race conditions where a port is found free but gets occupied
    before your server starts. The returned socket must be closed by the caller
    after the server has bound to the port.

    Args:
        port: Port to reserve (0 = let OS choose)

    Returns:
        Tuple[int, socket.socket]: (port_number, reserved_socket)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    actual_port = sock.getsockname()[1]
    return actual_port, sock


def find_free_port(
        port_min: int = 8000,
        port_max: int = 65535,
        excluded_ports: Optional[Set[int]] = None,
        port_filter: Optional[Callable[[int], bool]] = None
) -> Optional[int]:
    """
    Find a free port within the specified range on localhost.

    Args:
        port_min: Minimum port of the range (default: 8000)
        port_max: Maximum port of the range (default: 65535)
        excluded_ports: Set of ports to exclude from search
        port_filter: Optional callable that returns True if port should be considered

    Returns:
        int: First available port number, or None if no port is available
    """
    if excluded_ports is None:
        excluded_ports = set()

    # Validate port range
    if port_min < 1 or port_min > 65535:
        raise ValueError(f"port_min must be between 1 and 65535, got {port_min}")
    if port_max < 1 or port_max > 65535:
        raise ValueError(f"port_max must be between 1 and 65535, got {port_max}")
    if port_min > port_max:
        raise ValueError(f"port_min ({port_min}) must be <= port_max ({port_max})")

    for port in range(port_min, port_max + 1):
        # Skip excluded ports
        if port in excluded_ports:
            continue

        # Apply custom filter if provided
        if port_filter is not None and not port_filter(port):
            continue

        # Check if port is available
        if is_port_available(port):
            return port

    return None


def find_free_ports(
        count: int,
        port_min: int = 8000,
        port_max: int = 65535,
        excluded_ports: Optional[Set[int]] = None,
        port_filter: Optional[Callable[[int], bool]] = None
) -> list[int]:
    """
    Find multiple free ports within the specified range on localhost.

    Args:
        count: Number of free ports to find
        port_min: Minimum port of the range (default: 8000)
        port_max: Maximum port of the range (default: 65535)
        excluded_ports: Set of ports to exclude from search
        port_filter: Optional callable that returns True if port should be considered

    Returns:
        list[int]: List of available port numbers (may be less than count if not enough ports available)
    """
    if excluded_ports is None:
        excluded_ports = set()
    else:
        excluded_ports = excluded_ports.copy()

    found_ports = []

    for _ in range(count):
        port = find_free_port(
            port_min=port_min,
            port_max=port_max,
            excluded_ports=excluded_ports,
            port_filter=port_filter
        )

        if port is None:
            break

        found_ports.append(port)
        excluded_ports.add(port)

    return found_ports


def find_free_port_reserved(
        port_min: int = 8000,
        port_max: int = 65535,
        excluded_ports: Optional[Set[int]] = None,
        port_filter: Optional[Callable[[int], bool]] = None
) -> Optional[Tuple[int, socket.socket]]:
    """
    Find a free port and reserve it by keeping the socket open on localhost.

    This is a race-condition-safe version of find_free_port(). The returned socket
    must be closed by the caller after the server has bound to the port.

    Args:
        port_min: Minimum port of the range (default: 8000)
        port_max: Maximum port of the range (default: 65535)
        excluded_ports: Set of ports to exclude from search
        port_filter: Optional callable that returns True if port should be considered

    Returns:
        Optional[Tuple[int, socket.socket]]: (port, reserved_socket) or None if no port available
    """
    if excluded_ports is None:
        excluded_ports = set()

    # Validate port range
    if port_min < 1 or port_min > 65535:
        raise ValueError(f"port_min must be between 1 and 65535, got {port_min}")
    if port_max < 1 or port_max > 65535:
        raise ValueError(f"port_max must be between 1 and 65535, got {port_max}")
    if port_min > port_max:
        raise ValueError(f"port_min ({port_min}) must be <= port_max ({port_max})")

    for port_num in range(port_min, port_max + 1):
        # Skip excluded ports
        if port_num in excluded_ports:
            continue

        # Apply custom filter if provided
        if port_filter is not None and not port_filter(port_num):
            continue

        # Try to reserve the port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port_num))
            return port_num, sock
        except (OSError, socket.error):
            # Port is not available, try next one
            try:
                sock.close()
            except:
                pass
            continue

    return None


def find_free_ports_reserved(
        count: int,
        port_min: int = 8000,
        port_max: int = 65535,
        excluded_ports: Optional[Set[int]] = None,
        port_filter: Optional[Callable[[int], bool]] = None
) -> list[Tuple[int, socket.socket]]:
    """
    Find multiple free ports and reserve them by keeping sockets open on localhost.

    This is a race-condition-safe version of find_free_ports(). All returned sockets
    must be closed by the caller after servers have bound to the ports.

    Args:
        count: Number of free ports to find
        port_min: Minimum port of the range (default: 8000)
        port_max: Maximum port of the range (default: 65535)
        excluded_ports: Set of ports to exclude from search
        port_filter: Optional callable that returns True if port should be considered

    Returns:
        list[Tuple[int, socket.socket]]: List of (port, reserved_socket) tuples
    """
    if excluded_ports is None:
        excluded_ports = set()
    else:
        excluded_ports = excluded_ports.copy()

    found_ports = []

    for _ in range(count):
        result = find_free_port_reserved(
            port_min=port_min,
            port_max=port_max,
            excluded_ports=excluded_ports,
            port_filter=port_filter
        )

        if result is None:
            break

        port_num, sock = result
        found_ports.append((port_num, sock))
        excluded_ports.add(port_num)

    return found_ports
