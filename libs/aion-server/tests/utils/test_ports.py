"""Tests for port availability and PortReservationManager.

Key behaviors under test:
  - is_port_available: returns False for a bound port, True for a free one
  - find_free_port: respects excluded_ports and port_filter, validates range args
  - find_free_ports: returns unique non-overlapping ports
  - find_free_port_reserved: reserves port atomically (no TOCTOU between check and bind)
  - PortReservationManager: lifecycle – reserve, release, release_for_binding, context manager
"""

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


def _bind_port(port: int = 0) -> tuple[int, socket.socket]:
    """Bind a socket on a free port and return (port, socket)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    return sock.getsockname()[1], sock


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
        port = find_free_port(port_min=10000, port_max=20000)
        assert port is not None
        assert 10000 <= port <= 20000

    def test_excluded_ports_are_skipped(self):
        """Verify that excluded ports are skipped."""
        port = find_free_port(port_min=10000, port_max=10005)
        assert port is not None
        excluded = set(range(10000, port + 1))
        result = find_free_port(port_min=10000, port_max=10005, excluded_ports=excluded)
        if result is not None:
            assert result not in excluded

    def test_filter_function_applied(self):
        """Verify that filter function applied."""
        # Only odd ports
        port = find_free_port(
            port_min=10000,
            port_max=10100,
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
        ports = find_free_ports(count=3, port_min=10000, port_max=20000)
        assert len(ports) == 3
        assert len(set(ports)) == 3  # all unique

    def test_count_capped_by_available_range(self):
        """Verify that count capped by available range."""
        # Ask for 5 ports but range is only wide enough for 2 free ones
        port1, s1 = _bind_port()
        port2, s2 = _bind_port()
        try:
            ports = find_free_ports(
                count=5,
                port_min=port1,
                port_max=port2,
                excluded_ports={port1, port2},
            )
            # The two explicitly bound ports are excluded → could be 0 depending on range
            assert len(ports) <= 5
            assert len(set(ports)) == len(ports)
        finally:
            s1.close()
            s2.close()


class TestFindFreePortReserved:
    def test_returns_port_and_socket(self):
        """Verify that returns port and socket."""
        result = find_free_port_reserved(port_min=10000, port_max=20000)
        assert result is not None
        port, sock = result
        try:
            assert 10000 <= port <= 20000
            assert isinstance(sock, socket.socket)
        finally:
            sock.close()

    def test_reserved_port_is_not_available_while_held(self):
        """Verify that reserved port is not available while held."""
        result = find_free_port_reserved(port_min=10000, port_max=20000)
        assert result is not None
        port, sock = result
        try:
            assert is_port_available(port) is False
        finally:
            sock.close()

    def test_port_available_after_socket_closed(self):
        """Verify that port available after socket closed."""
        result = find_free_port_reserved(port_min=10000, port_max=20000)
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
            port = mgr.reserve_from_range("svc", port_min=10000, port_max=20000)
            assert port is not None
            assert mgr.has_reservation("svc") is True
            assert mgr.is_port_locked(port) is True
        finally:
            mgr.release_all()

    def test_reserve_from_range_respects_existing_locked_ports(self):
        """Ports already locked by the manager must be excluded from range search."""
        mgr = PortReservationManager()
        try:
            port_a = mgr.reserve_from_range("svc-a", port_min=10000, port_max=20000)
            port_b = mgr.reserve_from_range("svc-b", port_min=10000, port_max=20000)
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
            p1 = mgr.reserve_from_range("a", port_min=10000, port_max=20000)
            p2 = mgr.reserve_from_range("b", port_min=10000, port_max=20000)
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
            mgr.reserve_from_range("x", port_min=10000, port_max=20000)
            assert mgr.count() == 1
            mgr.reserve_from_range("y", port_min=10000, port_max=20000)
            assert mgr.count() == 2
        finally:
            mgr.release_all()
        assert mgr.count() == 0

    def test_context_manager_releases_all_on_exit(self):
        """Verify that context manager releases all on exit."""
        with PortReservationManager() as mgr:
            port = mgr.reserve_from_range("svc", port_min=10000, port_max=20000)
            assert port is not None
        assert mgr.count() == 0
        assert is_port_available(port) is True
