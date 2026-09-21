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

        self._log_handle = self.log_path.open("w", encoding="utf-8")
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
        """Kill the server process and close its log handle, but keep the directory."""
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

    def stop(self) -> None:
        """Interrupt the server, then kill whatever is left of its group.

        The working directory goes with it - the config was rendered and the
        log was already attached to whatever failed. ``KEEP_SERVE`` keeps
        both for a session someone is debugging.
        """
        self._stop_process()
        if not os.environ.get(KEEP_SERVE):
            shutil.rmtree(self._directory, ignore_errors=True)

    def restart(self) -> "ServeProcess":
        """Stop the server process and start a fresh one in the same directory.

        The config and directory survive; a new process is launched on the
        same port with the same environment. This is the operation a
        persistence test uses to verify that durable state outlives its
        server.
        """
        self._stop_process()
        self._log_handle = self.log_path.open("a", encoding="utf-8")
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
