"""Start a real `aion serve`, wait until it answers, and take it away again."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Optional

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from tests.scenarios.frameworks import Framework

__all__ = ["ServeProcess", "ServeVariant", "configs_root", "render_config"]

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

STARTUP_TIMEOUT_SECONDS = 30
READY_TIMEOUT_SECONDS = 90
SHUTDOWN_GRACE_SECONDS = 10

# Variables of the surrounding shell that would reach the platform or a
# database the scenarios never asked for. A variant that wants one passes it
# explicitly.
STRIPPED_ENV_PREFIXES = ("AION_", "CODEX_")
STRIPPED_ENV_NAMES = ("POSTGRES_URL", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


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
        enabled_extensions: URIs written into the agent's
            ``enabled_extensions``.
        env: Extra environment for the server process.
        expect_healthy: False for a variant that is supposed to start badly;
            readiness then only waits for the proxy itself.
        agent_ids: Agent ids the template declares.
    """

    name: str = "default"
    enabled_extensions: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    expect_healthy: bool = True
    agent_ids: tuple[str, ...] = (DEFAULT_AGENT_ID,)

    @property
    def agent_id(self) -> str:
        """The agent the scenarios talk to unless they say otherwise."""
        return self.agent_ids[0]


def _free_port() -> int:
    """A port nothing is listening on right now."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
    """The environment `aion serve` runs in: this shell, minus the platform."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(STRIPPED_ENV_PREFIXES) and name not in STRIPPED_ENV_NAMES
    }
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


class ServeProcess:
    """A running `aion serve` and the URLs it answers on.

    One process serves one (framework, variant) pair for the whole session.
    The scenarios reach the agents through the proxy, which is what a
    deployment exposes.
    """

    def __init__(self, framework: Framework, variant: ServeVariant) -> None:
        self.framework = framework
        self.variant = variant
        self.port = _free_port()
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
                "--port-range-start", str(self.port + 1),
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
