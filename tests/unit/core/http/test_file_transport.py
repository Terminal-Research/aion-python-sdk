"""File downloads reach public addresses only, at the address that was checked.

A file part may name any address, and the server downloads it for a model.
Each test names one way a caller could steer that download into the server's
own network, and shows it refused; the others show what still works. No test
leaves the machine: the network is a mock transport, and a host resolves to
the addresses a test chooses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time

import httpx
import pytest

from aion.core.agent import model_files
from aion.core.exceptions import ConversationFileError, TaskFailedError
from aion.core.http import file_transport
from aion.core.http.file_transport import (
    AddressNotPublicError,
    PublicAddressAsyncTransport,
    PublicAddressTransport,
    is_public_address,
)
from aion.core.runtime.context.conversation import fetch_file
from aion.core.settings import api_settings, file_settings

PUBLIC = "93.184.216.34"


class Network:
    """A host table and the requests that reached the mock network."""

    def __init__(self, hosts: dict[str, list[str]]) -> None:
        self.hosts = hosts
        self.lookups: list[str] = []
        self.sent: list[httpx.Request] = []
        self.answers: dict[str, httpx.Response] = {}

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return self.answers.get(request.headers["host"], httpx.Response(200, content=b"content"))

    async def resolve(self, host: str, port: int) -> list[str]:
        return self.resolve_sync(host, port)

    def resolve_sync(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        return self.hosts[host]

    def async_transport(self) -> PublicAddressAsyncTransport:
        return PublicAddressAsyncTransport(httpx.MockTransport(self.answer), resolve=self.resolve)

    def transport(self) -> PublicAddressTransport:
        return PublicAddressTransport(httpx.MockTransport(self.answer), resolve=self.resolve_sync)


@pytest.fixture
def network(monkeypatch) -> Network:
    """Every file download in this test goes to a mock network with this host table."""
    net = Network({"files.example.com": [PUBLIC]})
    monkeypatch.setattr(file_transport, "async_file_transport", net.async_transport)
    monkeypatch.setattr(file_transport, "file_transport", net.transport)
    monkeypatch.setattr(api_settings, "api_host", "https://api.aion.test")
    monkeypatch.setattr(file_settings, "file_trusted_hosts", "")
    return net


async def test_a_public_host_is_downloaded_from_the_address_that_was_checked(network) -> None:
    assert await fetch_file("https://files.example.com/report.pdf?grant=g", label="report.pdf") == b"content"

    (sent,) = network.sent
    assert sent.url.host == PUBLIC
    assert sent.url.path == "/report.pdf" and sent.url.query == b"grant=g"
    assert sent.headers["host"] == "files.example.com"
    assert sent.extensions["sni_hostname"] == "files.example.com"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # private
        "172.16.0.1",  # private
        "192.168.1.1",  # private
        "169.254.169.254",  # link-local: cloud metadata
        "100.64.0.1",  # shared address space (CGNAT)
        "0.0.0.0",  # this host
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fd00::1",  # IPv6 unique local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "64:ff9b::a01:203",  # NAT64 to 10.1.2.3
        "2002:a01:203::1",  # 6to4 from 10.1.2.3
    ],
)
async def test_a_host_on_a_non_public_address_is_refused(network, address) -> None:
    network.hosts["inside.example.com"] = [address]

    with pytest.raises(ConversationFileError, match="public addresses only") as refused:
        await fetch_file("https://inside.example.com/secret", label="secret.txt")

    assert "secret.txt" in str(refused.value) and "inside.example.com" not in str(refused.value)
    assert network.sent == []


async def test_one_non_public_address_among_public_ones_is_enough_to_refuse(network) -> None:
    network.hosts["mixed.example.com"] = [PUBLIC, "10.0.0.7"]

    with pytest.raises(ConversationFileError, match="public addresses only"):
        await fetch_file("https://mixed.example.com/f", label="f")
    assert network.sent == []


async def test_an_address_written_as_an_ip_is_checked_as_it_resolves() -> None:
    """With the real resolver: a literal address is still the address it names."""
    transport = PublicAddressAsyncTransport(httpx.MockTransport(lambda request: httpx.Response(200)))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        for url in ("http://127.0.0.1:9/", "http://[::1]/"):
            with pytest.raises(AddressNotPublicError):
                await client.get(url)


async def test_a_redirect_into_the_internal_network_is_refused(network) -> None:
    network.hosts["inside.example.com"] = ["10.0.0.5"]
    network.answers["files.example.com"] = httpx.Response(302, headers={"location": "http://inside.example.com/secret"})

    with pytest.raises(ConversationFileError, match="public addresses only"):
        await fetch_file("https://files.example.com/start", label="start")

    assert [request.headers["host"] for request in network.sent] == ["files.example.com"]


async def test_a_dns_answer_that_changes_after_the_check_is_not_used(network) -> None:
    """The connection goes to the address checked; the host is not looked up again."""
    answers = iter([[PUBLIC], ["10.0.0.5"]])
    network.hosts = {}

    async def rebinding(host: str, port: int) -> list[str]:
        network.lookups.append(host)
        return next(answers)

    transport = PublicAddressAsyncTransport(httpx.MockTransport(network.answer), resolve=rebinding)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        assert (await client.get("https://files.example.com/f")).status_code == 200

    assert network.lookups == ["files.example.com"]
    assert network.sent[0].url.host == PUBLIC


async def test_a_lookup_is_bounded_by_the_connect_timeout() -> None:
    async def never(host: str, port: int) -> list[str]:
        await asyncio.sleep(30)
        return [PUBLIC]

    transport = PublicAddressAsyncTransport(httpx.MockTransport(lambda request: httpx.Response(200)), resolve=never)
    started = time.monotonic()
    async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=httpx.Timeout(5, connect=0.05)) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get("https://files.example.com/f")
    assert time.monotonic() - started < 2


async def test_the_event_loop_keeps_serving_other_tasks_during_a_slow_lookup(monkeypatch) -> None:
    """The system lookup runs in the loop's thread pool, not on the loop."""

    def slow_getaddrinfo(host, port, *args, **kwargs):
        time.sleep(0.3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port))]

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    transport = PublicAddressAsyncTransport(httpx.MockTransport(lambda request: httpx.Response(200)))
    ticks = 0
    done = asyncio.Event()

    async def tick() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(tick())
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        assert (await client.get("https://files.example.com/f")).status_code == 200
    done.set()
    await ticker

    assert ticks >= 10


async def test_the_aion_api_host_and_trusted_hosts_are_reached_on_internal_addresses(network, monkeypatch) -> None:
    """A development deployment serves its stored files from localhost."""
    monkeypatch.setattr(api_settings, "api_host", "http://localhost:8081")
    monkeypatch.setattr(file_settings, "file_trusted_hosts", " Files.Corp.Internal. , other.internal")

    assert await fetch_file("http://localhost:8081/files/1/content", label="a") == b"content"
    assert await fetch_file("https://files.corp.internal/b", label="b") == b"content"

    assert network.lookups == []
    assert [request.url.host for request in network.sent] == ["localhost", "files.corp.internal"]


async def test_a_proxy_from_the_environment_is_not_used(network, monkeypatch) -> None:
    """A proxy would carry the request past the check, so downloads take none from the environment."""
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(variable, "http://proxy.example.com:3128")

    assert await fetch_file("https://files.example.com/f", label="f") == b"content"
    assert network.sent[0].url.host == PUBLIC


def test_a_blocking_read_for_a_model_is_checked_the_same_way(network) -> None:
    network.hosts["inside.example.com"] = ["169.254.169.254"]

    assert model_files._read_from_address("https://files.example.com/f", name="f") == b"content"
    with pytest.raises(TaskFailedError, match="public addresses only"):
        model_files._read_from_address("http://inside.example.com/latest/meta-data/", name="meta")
    assert [request.headers["host"] for request in network.sent] == ["files.example.com"]


async def test_a_download_is_logged_at_debug_by_its_host_not_its_link(network, caplog) -> None:
    """A stored file's link carries its own access; the log names the host only."""
    with caplog.at_level(logging.DEBUG, logger="aion.core.runtime.context.conversation"):
        await fetch_file("https://files.example.com/report.pdf?grant=secret", label="report.pdf")

    (record,) = [r for r in caplog.records if r.getMessage().startswith("Downloaded")]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == "Downloaded file 'report.pdf' (7 bytes) from files.example.com"


def test_a_blocking_download_is_logged_the_same_way(network, caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="aion.core.agent.model_files"):
        model_files._read_from_address("https://files.example.com/blocking.pdf?grant=secret", name="blocking.pdf")

    (record,) = [r for r in caplog.records if r.getMessage().startswith("Downloaded")]
    assert record.getMessage() == "Downloaded file 'blocking.pdf' (7 bytes) from files.example.com"


@pytest.mark.parametrize(
    "address, public",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("::ffff:8.8.8.8", True),
        ("2001::4136:e378:8000:63bf:f7f7:f7f7", True),  # Teredo to 8.8.8.8
        ("2001::4136:e378:8000:63bf:f5fe:fdfe", False),  # Teredo to 10.1.2.1
        ("198.51.100.7", False),  # documentation range
    ],
)
def test_an_ipv6_address_is_judged_by_the_ipv4_address_it_carries(address, public) -> None:
    assert is_public_address(ipaddress.ip_address(address)) is public
