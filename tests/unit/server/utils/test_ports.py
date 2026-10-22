"""Tests for port availability and PortReservationManager.

Key behaviors under test:
  - is_port_available: returns False for a bound port, True for a free one
  - find_free_port: respects excluded_ports and port_filter, validates range args
  - find_free_ports: returns unique non-overlapping ports
  - find_free_port_reserved: reserves port atomically (no TOCTOU between check and bind)
  - PortReservationManager: lifecycle – reserve, release, release_for_binding, context manager
"""

import os
import socket
import pytest

from aion.server.utils.ports.availability import (
    find_free_port,
    find_free_ports,
    find_free_port_reserved,
    is_port_available,
    reserve_port,
)
from aion.server.utils.ports.reservation import PortReservationManager


def _worker_ports() -> range:
    """The ports this module's tests use, disjoint across pytest-xdist workers.

    The searches under test scan their range from the lowest port up, so tests
    scanning one range from parallel workers compete for the same few ports: a
    test that frees a port and checks it is free fails when a test in another
    worker has just taken it. Each worker gets a thousand ports of its own
    above 10000, below the ephemeral range of every OS, so no outgoing
    connection takes one either; tests within a worker run one at a time.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    start = 10000 + int(worker.removeprefix("gw")) * 1000
    return range(start, start + 1000)


PORTS = _worker_ports()
PORT_MIN, PORT_MAX = PORTS[0], PORTS[-1]


def _bind_port() -> tuple[int, socket.socket]:
    """Occupy a free port of this worker's range the way a real server does.

    The `listen` is what makes the port occupied, not the `bind`: on Linux two
    SO_REUSEADDR sockets may hold the same address as long as neither is
    listening, so a bound-but-idle socket leaves `is_port_available` answering
    True. (BSD/macOS refuses the second bind outright, which is why a test
    written without the `listen` passes there and fails here.)

    Returns:
        The port and the listening socket that holds it.
    """
    for port in PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            sock.listen(1)
        except OSError:
            sock.close()
            continue
        return port, sock
    raise RuntimeError(f"no free port in {PORT_MIN}-{PORT_MAX}")


class TestIsPortAvailable:
    def test_free_port_is_available(self):
        """Verify that free port is available."""
        port, sock = _bind_port()
        sock.close()
        # After closing the socket a fresh port should be available
        assert is_port_available(port) is True

    def test_bound_port_is_not_available(self):
        """Verify that bound port is not available."""
        port, sock = _bind_port()
        try:
            assert is_port_available(port) is False
        finally:
            sock.close()


class TestFindFreePort:
    def test_returns_port_in_range(self):
        """Verify that returns port in range."""
        port = find_free_port(port_min=PORT_MIN, port_max=PORT_MAX)
        assert port is not None
        assert PORT_MIN <= port <= PORT_MAX

    def test_excluded_ports_are_skipped(self):
        """Verify that excluded ports are skipped."""
        port = find_free_port(port_min=PORT_MIN, port_max=PORT_MIN + 5)
        assert port is not None
        excluded = set(range(PORT_MIN, port + 1))
        result = find_free_port(port_min=PORT_MIN, port_max=PORT_MIN + 5, excluded_ports=excluded)
        if result is not None:
            assert result not in excluded

    def test_filter_function_applied(self):
        """Verify that filter function applied."""
        # Only odd ports
        port = find_free_port(
            port_min=PORT_MIN,
            port_max=PORT_MIN + 100,
            port_filter=lambda p: p % 2 == 1,
        )
        assert port is None or port % 2 == 1

    def test_port_min_greater_than_max_raises(self):
        """Verify that port min greater than max raises."""
        with pytest.raises(ValueError):
            find_free_port(port_min=9000, port_max=8000)

    def test_port_min_below_1_raises(self):
        """Verify that port min below 1 raises."""
        with pytest.raises(ValueError):
            find_free_port(port_min=0, port_max=8000)

    def test_no_port_available_returns_none(self):
        """Verify that no port available returns none."""
        # Reserve one port and exclude it; use single-port range
        port, sock = _bind_port()
        try:
            result = find_free_port(port_min=port, port_max=port)
            assert result is None
        finally:
            sock.close()


class TestFindFreePorts:
    def test_returns_unique_ports(self):
        """Verify that returns unique ports."""
        ports = find_free_ports(count=3, port_min=PORT_MIN, port_max=PORT_MAX)
        assert len(ports) == 3
        assert len(set(ports)) == 3  # all unique

    def test_count_capped_by_available_range(self):
        """Asking for more than the range holds returns what fits, not what was asked.

        The range is a fixed window with all but two of its ports excluded, so
        the ceiling is known regardless of what the machine happens to be
        running: at most those two can come back, against a request for five.
        Deriving the window from OS-assigned ephemeral ports instead would be
        flaky - the kernel hands them out in no particular order, so the pair
        lands reversed often enough, and find_free_port rejects an inverted
        range rather than searching it.
        """
        port_min, port_max = 20000, 20009
        allowed = {port_min, port_max}
        excluded = set(range(port_min, port_max + 1)) - allowed

        ports = find_free_ports(
            count=5,
            port_min=port_min,
            port_max=port_max,
            excluded_ports=excluded,
        )

        assert len(ports) <= len(allowed)
        assert set(ports) <= allowed
        assert len(set(ports)) == len(ports)


class TestFindFreePortReserved:
    def test_returns_port_and_socket(self):
        """Verify that returns port and socket."""
        result = find_free_port_reserved(port_min=PORT_MIN, port_max=PORT_MAX)
        assert result is not None
        port, sock = result
        try:
            assert PORT_MIN <= port <= PORT_MAX
            assert isinstance(sock, socket.socket)
        finally:
            sock.close()

    def test_reserved_port_is_not_available_while_held(self):
        """Verify that reserved port is not available while held."""
        result = find_free_port_reserved(port_min=PORT_MIN, port_max=PORT_MAX)
        assert result is not None
        port, sock = result
        try:
            assert is_port_available(port) is False
        finally:
            sock.close()

    def test_port_available_after_socket_closed(self):
        """Verify that port available after socket closed."""
        result = find_free_port_reserved(port_min=PORT_MIN, port_max=PORT_MAX)
        assert result is not None
        port, sock = result
        sock.close()
        assert is_port_available(port) is True


class TestPortReservationManager:
    def test_reserve_specific_port(self):
        """Verify that reserve specific port."""
        mgr = PortReservationManager()
        port, sock = _bind_port()
        sock.close()  # free it so manager can reserve it
        try:
            ok = mgr.reserve("svc", port)
            assert ok is True
            assert mgr.get("svc") == port
            assert mgr.is_port_locked(port) is True
        finally:
            mgr.release_all()

    def test_reserve_duplicate_key_returns_false(self):
        """Verify that reserve duplicate key returns false."""
        mgr = PortReservationManager()
        port, sock = _bind_port()
        sock.close()
        mgr.reserve("svc", port)
        try:
            ok = mgr.reserve("svc", port)
            assert ok is False
        finally:
            mgr.release_all()

    def test_reserve_already_locked_port_returns_false(self):
        """Verify that reserve already locked port returns false."""
        mgr = PortReservationManager()
        port, sock = _bind_port()
        sock.close()
        mgr.reserve("svc-a", port)
        try:
            ok = mgr.reserve("svc-b", port)
            assert ok is False
        finally:
            mgr.release_all()

    def test_reserve_from_range_finds_and_locks_port(self):
        """Verify that reserve from range finds and locks port."""
        mgr = PortReservationManager()
        try:
            port = mgr.reserve_from_range("svc", port_min=PORT_MIN, port_max=PORT_MAX)
            assert port is not None
            assert mgr.has_reservation("svc") is True
            assert mgr.is_port_locked(port) is True
        finally:
            mgr.release_all()

    def test_reserve_from_range_respects_existing_locked_ports(self):
        """Ports already locked by the manager must be excluded from range search."""
        mgr = PortReservationManager()
        try:
            port_a = mgr.reserve_from_range("svc-a", port_min=PORT_MIN, port_max=PORT_MAX)
            port_b = mgr.reserve_from_range("svc-b", port_min=PORT_MIN, port_max=PORT_MAX)
            assert port_a is not None
            assert port_b is not None
            assert port_a != port_b
        finally:
            mgr.release_all()

    def test_release_closes_socket_and_removes_lock(self):
        """Verify that release closes socket and removes lock."""
        mgr = PortReservationManager()
        port, s = _bind_port()
        s.close()
        mgr.reserve("svc", port)
        assert mgr.is_port_locked(port) is True
        mgr.release("svc")
        assert mgr.has_reservation("svc") is False
        assert mgr.is_port_locked(port) is False
        assert is_port_available(port) is True

    def test_release_unknown_key_returns_false(self):
        """Verify that release unknown key returns false."""
        mgr = PortReservationManager()
        assert mgr.release("nonexistent") is False

    def test_release_for_binding_keeps_port_locked(self):
        """After release_for_binding the socket is gone but the lock remains,
        preventing a second reservation of the same port."""
        mgr = PortReservationManager()
        port, s = _bind_port()
        s.close()
        mgr.reserve("svc", port)
        released_port = mgr.release_for_binding("svc")
        assert released_port == port
        assert mgr.has_reservation("svc") is False
        assert mgr.is_port_locked(port) is True  # still locked!

    def test_get_all_returns_key_port_mapping(self):
        """Verify that get all returns key port mapping."""
        mgr = PortReservationManager()
        try:
            p1 = mgr.reserve_from_range("a", port_min=PORT_MIN, port_max=PORT_MAX)
            p2 = mgr.reserve_from_range("b", port_min=PORT_MIN, port_max=PORT_MAX)
            all_ports = mgr.get_all()
            assert all_ports["a"] == p1
            assert all_ports["b"] == p2
        finally:
            mgr.release_all()

    def test_count_tracks_active_reservations(self):
        """Verify that count tracks active reservations."""
        mgr = PortReservationManager()
        assert mgr.count() == 0
        try:
            mgr.reserve_from_range("x", port_min=PORT_MIN, port_max=PORT_MAX)
            assert mgr.count() == 1
            mgr.reserve_from_range("y", port_min=PORT_MIN, port_max=PORT_MAX)
            assert mgr.count() == 2
        finally:
            mgr.release_all()
        assert mgr.count() == 0

    def test_context_manager_releases_all_on_exit(self):
        """Verify that context manager releases all on exit."""
        with PortReservationManager() as mgr:
            port = mgr.reserve_from_range("svc", port_min=PORT_MIN, port_max=PORT_MAX)
            assert port is not None
        assert mgr.count() == 0
        assert is_port_available(port) is True
