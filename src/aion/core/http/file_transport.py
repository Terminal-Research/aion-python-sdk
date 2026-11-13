"""HTTP transports for downloading a file at an address a message names.

A file part may carry any address, and reading the file for a model makes the
server fetch it. The server must not reach into its own network on behalf of
whoever sent the message, so these transports fetch only public addresses:

- the host is resolved here, and the request is refused when any address it
  resolves to is not a public one (private networks, loopback, link-local -
  cloud metadata among them -, shared and reserved ranges, multicast, and the
  same in IPv6, including IPv4 addresses carried inside IPv6 ones);
- the connection goes to the address that was checked, while the host name
  stays in the ``Host`` header and in TLS (SNI and certificate checks), so a
  DNS answer cannot change between the check and the connection;
- a redirect is a new request through the same transport and is checked the
  same way.

Two kinds of host are fetched without the check: the Aion API host
(``AION_API_HOST``), which serves the deployment's stored files and is on
localhost in development, and the hosts listed in ``AION_FILE_TRUSTED_HOSTS``.

A client built on these transports must not take proxies from the
environment (``trust_env=False``): a proxy mount would send the request past
the transport.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Optional, Union
from urllib.parse import urlsplit

import httpx

from aion.core.exceptions import AionError
from aion.core.settings import api_settings, file_settings

__all__ = [
    "AddressNotPublicError",
    "PublicAddressAsyncTransport",
    "PublicAddressTransport",
    "async_file_transport",
    "file_transport",
    "is_public_address",
]

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
"""IPv6 addresses that a NAT64 gateway translates to the IPv4 address in their last 32 bits."""


class AddressNotPublicError(AionError):
    """A file's address leads to a host that is not on the public internet.

    The message names the host, which the reader of a server's log may see;
    whoever reports it to a caller names the file instead.
    """


def is_public_address(address: IPAddress) -> bool:
    """Whether ``address`` is a public one, which a file may be fetched from.

    An IPv6 address that carries an IPv4 one (IPv4-mapped, NAT64, 6to4,
    Teredo) is judged by the IPv4 address it leads to.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public_address(address.ipv4_mapped)
        if address in _NAT64:
            return is_public_address(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
        if address.sixtofour is not None:
            return is_public_address(address.sixtofour)
        if address.teredo is not None:
            return is_public_address(address.teredo[1])
    return address.is_global and not address.is_multicast


def _trusted(host: str) -> bool:
    """Whether ``host`` is fetched without the check: the Aion API host or a listed one."""
    host = host.lower().rstrip(".")
    api_host = (urlsplit(api_settings.api_host).hostname or "").lower()
    return host == api_host or host in file_settings.trusted_hosts


def _port(url: httpx.URL) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


def _checked(host: str, addresses: Sequence[str]) -> IPAddress:
    """The address to connect to, once every address ``host`` resolved to is public."""
    parsed = [ipaddress.ip_address(address.split("%", 1)[0]) for address in addresses]
    if not parsed:
        raise AddressNotPublicError(f"Host {host!r} resolved to no address.")
    refused = [str(address) for address in parsed if not is_public_address(address)]
    if refused:
        raise AddressNotPublicError(
            f"Host {host!r} resolves to an address that is not public ({', '.join(refused)}); "
            "files are fetched from public addresses only."
        )
    return parsed[0]


def _pinned(request: httpx.Request, address: IPAddress) -> httpx.Request:
    """``request`` sent to ``address``, with its host name kept for the server and for TLS."""
    return httpx.Request(
        request.method,
        request.url.copy_with(host=str(address)),
        headers=request.headers,
        stream=request.stream,
        extensions={**request.extensions, "sni_hostname": request.url.host},
    )


async def _resolve_async(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


class PublicAddressAsyncTransport(httpx.AsyncBaseTransport):
    """An async transport that fetches public addresses only, connecting to the address it checked.

    Args:
        inner: The transport that sends the checked request; a new
            ``httpx.AsyncHTTPTransport`` when not given.
        resolve: How a host name becomes its addresses; the event loop's
            ``getaddrinfo`` when not given.
    """

    def __init__(
            self,
            inner: Optional[httpx.AsyncBaseTransport] = None,
            *,
            resolve: Optional[Callable[[str, int], Awaitable[list[str]]]] = None,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._resolve = resolve or _resolve_async

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if _trusted(host):
            return await self._inner.handle_async_request(request)
        # The lookup runs in the event loop's thread pool, so the loop serves
        # other tasks meanwhile. It is bounded by the request's connect
        # timeout, which covered the lookup when httpcore made it; a lookup
        # that outlives the wait finishes in its thread, unused.
        connect_timeout = request.extensions.get("timeout", {}).get("connect")
        try:
            addresses = await asyncio.wait_for(self._resolve(host, _port(request.url)), connect_timeout)
        except asyncio.TimeoutError as error:
            raise httpx.ConnectTimeout(
                f"Host {host!r} was not resolved within {connect_timeout} seconds.", request=request
            ) from error
        except OSError as error:
            raise httpx.ConnectError(f"Host {host!r} could not be resolved.", request=request) from error
        return await self._inner.handle_async_request(_pinned(request, _checked(host, addresses)))

    async def aclose(self) -> None:
        await self._inner.aclose()


class PublicAddressTransport(httpx.BaseTransport):
    """The blocking counterpart of :class:`PublicAddressAsyncTransport`.

    Args:
        inner: The transport that sends the checked request; a new
            ``httpx.HTTPTransport`` when not given.
        resolve: How a host name becomes its addresses; ``socket.getaddrinfo``
            when not given.
    """

    def __init__(
            self,
            inner: Optional[httpx.BaseTransport] = None,
            *,
            resolve: Optional[Callable[[str, int], list[str]]] = None,
    ) -> None:
        self._inner = inner or httpx.HTTPTransport()
        self._resolve = resolve or _resolve

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if _trusted(host):
            return self._inner.handle_request(request)
        # A blocking lookup has no timeout of its own here, as in httpcore's
        # blocking connection, whose timeout covers the connect, not the lookup.
        try:
            addresses = self._resolve(host, _port(request.url))
        except OSError as error:
            raise httpx.ConnectError(f"Host {host!r} could not be resolved.", request=request) from error
        return self._inner.handle_request(_pinned(request, _checked(host, addresses)))

    def close(self) -> None:
        self._inner.close()


def async_file_transport() -> httpx.AsyncBaseTransport:
    """The transport every async file download goes through."""
    return PublicAddressAsyncTransport()


def file_transport() -> httpx.BaseTransport:
    """The transport every blocking file download goes through."""
    return PublicAddressTransport()
