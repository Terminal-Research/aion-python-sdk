"""Start a real `aion serve`, wait until it answers, and take it away again."""

from __future__ import annotations

import contextlib
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Union

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from tests.scenarios.frameworks import Framework

from .control_plane import control_plane

__all__ = [
    "AGENT_PORTS",
    "PROXY_PORTS",
    "ServeProcess",
    "ServeVariant",
    "configs_root",
    "proxy_port",
    "render_config",
]

DEFAULT_AGENT_ID = "scenario-agent"
KEEP_SERVE = "KEEP_SERVE"
"""Set it to leave a stopped server's directory - config and log - on disk."""

AION_BIN = "SCENARIOS_AION_BIN"
"""The `aion` to start, when it is not the one beside this interpreter."""

REPO_ROOT = Path(__file__).resolve().parents[3]
"""Repository root, which the server process needs on its import path.

The agents are modules of ``tests.scenarios`` and the server imports them by
the path ``aion.yaml`` names. Nothing about a child `aion serve` would put
this repository on its ``sys.path``, so the harness does.
"""

AGENT_PORTS = (20000, 29999)
"""The ports every server's agents take, given to `aion serve` as its port range.

`aion serve --port P` without a range of its own puts its agents on P + 1 and
up, and the harness hands out proxy ports in sequence (``proxy_port``), so
P + 1 is the proxy port of the next server it creates. This range lies apart
from ``PROXY_PORTS`` and below the ports the kernel picks for a socket bound
to port 0 (32768 and up on Linux, 49152 and up on macOS), where the stand-in
control plane and the push callbacks listen. Servers share it safely: `aion
serve` holds each agent port from the moment it finds it free, so two servers
starting at once take different ones.
"""

PROXY_PORTS = (30000, 32767)
"""The ports the proxies listen on, split between the pytest-xdist workers.

`aion serve --port P` reserves P with a socket of its own a second or two
after it starts, so the harness can hold nothing on P in the meantime. A
port the kernel picked for a socket bound to port 0 is free for the kernel
to give to the next socket that asks in that window, and `aion serve` then
fails to reserve it. This range lies above ``AGENT_PORTS`` and below the
ports the kernel picks (32768 and up on Linux, 49152 and up on macOS), so on
a machine with the default port settings only the harness takes ports from
it. Each worker hands out ports from a slice of its own, so two workers
never give out the same one.
"""

STARTUP_TIMEOUT_SECONDS = 30
READY_TIMEOUT_SECONDS = 90
SHUTDOWN_GRACE_SECONDS = 40
"""How long an interrupted `aion serve` has to exit before its group is killed.

Longer than an agent's own shutdown budget (``AION_SHUTDOWN_TIMEOUT``, 25 s by
default): a scenario about a turn that does not stop in time waits the whole of it.
"""

# What a server process takes from the surrounding shell: where programs are,
# whose home it is, how text is encoded, where temporary files go. Nothing
# else reaches it - a developer's credentials, database or storage backend
# would otherwise decide what a scenario tests, and a setting added to the SDK
# tomorrow is kept out without anyone listing it. A variant passes what it
# wants explicitly.
INHERITED_ENV_NAMES = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    # Joined with this repository below, so the agents under tests/ import.
    "PYTHONPATH",
    # Windows keeps the same facts under its own names.
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
})
INHERITED_ENV_PREFIXES = ("LC_",)


def configs_root() -> Path:
    """Directory holding the `aion.yaml` templates."""
    return Path(__file__).resolve().parent.parent / "configs"


def render_config(variant: str, **context: object) -> str:
    """Render one `aion.yaml` template."""
    environment = Environment(
        loader=FileSystemLoader(configs_root()),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return environment.get_template(f"{variant}.yaml.j2").render(**context)


@dataclass(frozen=True)
class ServeVariant:
    """One deployment shape: which template, with which extensions and environment.

    Attributes:
        name: Template name under ``configs/``, without the ``.yaml.j2``.
        enabled_extensions: The entries written into the agent's
            ``enabled_extensions``: a URI, or a mapping of a URI to the
            options of an executable extension.
        env: Extra environment for the server process.
        expect_healthy: False for a variant that is supposed to start badly;
            readiness then only waits for the proxy itself.
        agent_ids: Agent ids the template declares.
    """

    name: str = "default"
    enabled_extensions: tuple[Union[str, Mapping[str, Any]], ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    expect_healthy: bool = True
    agent_ids: tuple[str, ...] = (DEFAULT_AGENT_ID,)

    @property
    def agent_id(self) -> str:
        """The agent the scenarios talk to unless they say otherwise."""
        return self.agent_ids[0]


def _worker_proxy_ports() -> range:
    """This worker's slice of ``PROXY_PORTS``, disjoint from every other worker's.

    pytest-xdist names its workers ``gw0``, ``gw1``, ... and tells each how
    many there are; without xdist the one process has the whole range. A
    worker xdist starts in place of a crashed one has a number past the
    count and shares a slice, where the bind check in ``proxy_port`` keeps
    it off the ports in use.
    """
    count = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    index = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw")) % count
    size = (PROXY_PORTS[1] - PROXY_PORTS[0] + 1) // count
    start = PROXY_PORTS[0] + index * size
    return range(start, start + size)


_PROXY_PORTS = _worker_proxy_ports()
# Where the next search starts. A random place in the slice, so two runs on
# one machine - two checkouts at once, whose workers share slice numbers -
# seldom try the same port at the same moment.
_proxy_cursor = random.SystemRandom().randrange(len(_PROXY_PORTS))


def _bindable(port: int) -> bool:
    """Whether nothing holds ``port``: a bind to it on every address succeeds.

    No ``SO_REUSEADDR``: with it, macOS lets the wildcard bind through while
    another program listens on the port on loopback, the address the
    scenarios connect to.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
        return True


def proxy_port(neighbours: int = 1) -> int:
    """A port of this worker's slice of ``PROXY_PORTS`` for a proxy to listen on.

    The search walks the slice in order from where the last one stopped,
    wrapping at its end, and returns the first port that binds. A port given
    out is not tried again until the walk has gone round the whole slice, so
    two servers created before either starts get different ports.

    Args:
        neighbours: How many consecutive ports, all free, the returned one
            starts; the walk moves past all of them.

    Returns:
        The lowest of those ports.
    """
    global _proxy_cursor
    for _ in range(len(_PROXY_PORTS)):
        first = _proxy_cursor
        _proxy_cursor = (first + 1) % len(_PROXY_PORTS)
        ports = _PROXY_PORTS[first:first + neighbours]
        if len(ports) == neighbours and all(_bindable(port) for port in ports):
            _proxy_cursor = (first + neighbours) % len(_PROXY_PORTS)
            return ports[0]
    raise RuntimeError(
        f"no {neighbours} consecutive free ports in {_PROXY_PORTS[0]}-{_PROXY_PORTS[-1]}, "
        "this worker's part of PROXY_PORTS"
    )


def _descendants(pid: int) -> list[int]:
    """Every process under this one, parents before children.

    ``pgrep -P`` rather than a process library: it is on every POSIX system
    the scenarios run on, and this is the only thing here that needs to look
    at the process tree at all.
    """
    found: list[int] = []
    frontier = [pid]
    while frontier:
        parent = frontier.pop(0)
        result = subprocess.run(
            ["pgrep", "-P", str(parent)], capture_output=True, text=True, check=False
        )
        children = [int(line) for line in result.stdout.split() if line.isdigit()]
        found.extend(children)
        frontier.extend(children)
    return found


def _server_env(variant: ServeVariant) -> dict[str, str]:
    """The environment `aion` runs in: this shell's basics, the stand-in control plane, the variant's.

    `aion` reads no `.env`: a command the harness runs without a directory of
    its own runs in the test's, which may hold the developer's.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in INHERITED_ENV_NAMES or name.startswith(INHERITED_ENV_PREFIXES)
    }
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    # The server finds the keys anonymous session tokens are verified with here.
    environment["AION_API_HOST"] = control_plane().url
    environment.update(variant.env)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{existing}" if existing else str(REPO_ROOT)
    return environment


def _aion_executable() -> Path:
    """The `aion` console script to start, and which installation it comes from.

    By default the one beside the interpreter running the tests - the project
    environment, and with it the working tree. ``SCENARIOS_AION_BIN`` points
    the scenarios at another installation instead: that is how
    ``make tests-scenarios-dist`` drives the wheel it just built.
    """
    override = os.environ.get(AION_BIN)
    if override:
        executable = Path(override)
        if not executable.is_file():
            raise RuntimeError(f"{AION_BIN} is set to {override!r}, which is not a file")
        return executable
    executable = Path(sys.executable).parent / "aion"
    if not executable.exists():
        raise RuntimeError(
            f"`aion` is not installed next to {sys.executable}. Install the project with "
            "`poetry install -E langgraph-server -E adk-server --with dev` and run the "
            "scenarios inside its environment."
        )
    return executable


def run_aion(*args: str, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one `aion` command to completion, as an operator would, and return what it said.

    Args:
        *args: The command line after ``aion``, such as ``("db", "check")``.
        env: Extra environment, such as ``POSTGRES_URL``.

    Returns:
        The finished process, its stdout and stderr merged into ``stdout``.
    """
    return subprocess.run(
        [str(_aion_executable()), *args],
        env=_server_env(ServeVariant(name="command", env=env)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=READY_TIMEOUT_SECONDS,
        check=False,
    )


class ServeProcess:
    """A running `aion serve` and the URLs it answers on.

    One process serves one (framework, variant) pair for the whole session.
    The scenarios reach the agents through the proxy, which is what a
    deployment exposes. The proxy listens on ``port`` when one is given, and
    on one from ``proxy_port`` otherwise; the agents on ports in
    ``AGENT_PORTS``.
    """

    def __init__(self, framework: Framework, variant: ServeVariant, port: Optional[int] = None) -> None:
        self.framework = framework
        self.variant = variant
        self.port = port if port is not None else proxy_port()
        self._directory = Path(tempfile.mkdtemp(prefix=f"aion-scenarios-{framework.name}-{variant.name}-"))
        self._process: Optional[subprocess.Popen] = None
        self._log_handle = None

    @property
    def base_url(self) -> str:
        """Proxy root."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def directory(self) -> Path:
        """Working directory of the server, holding its `aion.yaml` and log."""
        return self._directory

    @property
    def log_path(self) -> Path:
        """File both server streams are written to."""
        return self._directory / "serve.log"

    @property
    def config_path(self) -> Path:
        """The rendered `aion.yaml` the server was started with."""
        return self._directory / "aion.yaml"

    def agent_url(self, agent_id: Optional[str] = None) -> str:
        """Proxy URL of one agent."""
        return f"{self.base_url}/agents/{agent_id or self.variant.agent_id}"

    def start(self) -> "ServeProcess":
        """Render the config, launch the server, and wait until it answers."""
        self.config_path.write_text(
            render_config(
                self.variant.name,
                framework=self.framework.name,
                agent_path=self.framework.agent_path,
                agent_id=self.variant.agent_id,
                agent_ids=list(self.variant.agent_ids),
                enabled_extensions=list(self.variant.enabled_extensions),
            ),
            encoding="utf-8",
        )
        return self._launch("w")

    def _launch(self, log_mode: str) -> "ServeProcess":
        """Start a server process on the rendered config and wait until it answers.

        Args:
            log_mode: How to open the log - ``"w"`` for the first process of a
                directory, ``"a"`` for a successor, whose own output is
                appended after its predecessor's.
        """
        self._log_handle = self.log_path.open(log_mode, encoding="utf-8")
        self._process = subprocess.Popen(
            [
                str(_aion_executable()),
                "serve",
                "--port", str(self.port),
                "--port-range-start", str(AGENT_PORTS[0]),
                "--port-range-end", str(AGENT_PORTS[1]),
                "--startup-timeout", str(STARTUP_TIMEOUT_SECONDS),
            ],
            cwd=self._directory,
            env=_server_env(self.variant),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        try:
            self._wait_until_ready()
        except Exception:
            self.stop()
            raise
        return self

    def _wait_until_ready(self) -> None:
        """Poll the proxy until every agent reports healthy."""
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        last_seen = "no answer yet"
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"`aion serve` exited with code {self._process.returncode} before answering.\n"
                    f"{self.log_tail()}"
                )
            try:
                response = httpx.get(f"{self.base_url}/health/system/", timeout=5.0)
            except httpx.HTTPError as error:
                last_seen = f"{type(error).__name__}: {error}"
            else:
                if response.status_code == 200:
                    payload = response.json()
                    if not self.variant.expect_healthy:
                        return
                    if payload.get("overall_agents_status") == "healthy":
                        return
                    last_seen = str(payload)
                else:
                    last_seen = f"HTTP {response.status_code}"
            time.sleep(0.25)

        raise TimeoutError(
            f"`aion serve` for {self.framework.name}/{self.variant.name} was not healthy within "
            f"{READY_TIMEOUT_SECONDS}s (last: {last_seen}).\n{self.log_tail()}"
        )

    def log_tail(self, lines: int = 60) -> str:
        """The end of the server log, ready to be attached to a failure."""
        if not self.log_path.exists():
            return f"--- no log at {self.log_path} ---"
        text = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(text[-lines:])
        return f"--- {self.log_path} (last {lines} lines) ---\n{tail}\n--- end of log ---"

    def _stop_process(self) -> None:
        """Interrupt the server process and close its log handle, keeping the directory.

        SIGINT to the whole group, which is the signal an operator sends: the
        server drains what it is running and settles the tasks it cancels. A
        group that is still there after the grace period is killed, so a wedged
        process cannot hold the session.
        """
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), signal.SIGINT)
            try:
                process.wait(timeout=SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=SHUTDOWN_GRACE_SECONDS)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _kill_process(self) -> None:
        """Take the server away with no chance to shut down: SIGKILL to the whole tree.

        Not ``killpg`` on its own. ``aion serve`` runs each agent as the leader
        of a process group of its own (``ProcessManager``), so signalling the
        launcher's group reaches the launcher and the proxy and leaves every
        agent running - and it is the agent that holds the task. The
        descendants are collected first, while their parent is still there to
        name them.
        """
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            for pid in reversed(_descendants(process.pid)):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=SHUTDOWN_GRACE_SECONDS)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def stop(self) -> None:
        """Interrupt the server, then kill whatever is left of its group.

        The working directory goes with it - the config was rendered and the
        log was already attached to whatever failed. ``KEEP_SERVE`` keeps
        both for a session someone is debugging.
        """
        self._stop_process()
        if not os.environ.get(KEEP_SERVE):
            shutil.rmtree(self._directory, ignore_errors=True)

    def kill(self) -> "ServeProcess":
        """Take the server away outright, and do not start anything in its place.

        The difference from ``crash_restart`` is the whole point of the
        scenarios that use it: nothing replaces the dead process, so whatever
        becomes of the tasks it was holding is the work of the other servers
        that were already running.
        """
        self._kill_process()
        return self

    def restart(self) -> "ServeProcess":
        """Shut the server down and start a fresh one in the same directory.

        The config and directory survive; a new process is launched on the
        same port with the same environment. This is the orderly restart - a
        deployment rolling over - so the outgoing process runs its shutdown
        and settles whatever it was executing.
        """
        self._stop_process()
        return self._launch("a")

    def crash_restart(self) -> "ServeProcess":
        """Kill the server outright and start a fresh one in the same directory.

        The counterpart of ``restart``: SIGKILL leaves no chance to shut
        anything down, so a task that was running stays in the store with the
        active state it had. What the next process makes of it is the
        guarantee a scenario checks - the settlement reason is the only thing
        that tells the two restarts apart.
        """
        self._kill_process()
        return self._launch("a")

    def __enter__(self) -> "ServeProcess":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


@contextlib.contextmanager
def serve(framework: Framework, variant: ServeVariant) -> Iterator[ServeProcess]:
    """Run one server for the duration of the block."""
    process = ServeProcess(framework, variant)
    try:
        yield process.start()
    finally:
        process.stop()
