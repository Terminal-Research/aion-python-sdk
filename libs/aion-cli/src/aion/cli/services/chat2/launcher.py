"""Launch the experimental standalone ``chat2`` UI."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.resources
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Callable, Optional


class BinaryResolutionError(RuntimeError):
    """Raised when the standalone chat UI entrypoint cannot be located."""


@dataclass(frozen=True)
class Chat2LaunchOptions:
    """Arguments forwarded from ``aion chat2`` to the standalone UI.

    Args:
        endpoint: Optional direct or proxied A2A endpoint URL.
        agent_id: Optional agent identifier for proxy-aware routing.
        token: Optional bearer token.
        headers: Additional HTTP headers.
        push_notifications: Whether to start the push notification receiver.
        push_receiver: Push notification receiver URL.
    """

    endpoint: Optional[str]
    agent_id: Optional[str]
    token: Optional[str]
    headers: dict[str, str]
    push_notifications: bool
    push_receiver: str

    def to_args(self) -> list[str]:
        """Convert the options to CLI arguments for the standalone UI."""
        args: list[str] = []

        if self.endpoint:
            args.extend(["--url", self.endpoint])
        if self.agent_id:
            args.extend(["--agent-id", self.agent_id])
        if self.token:
            args.extend(["--token", self.token])
        for key, value in self.headers.items():
            args.extend(["--header", f"{key}={value}"])
        if self.push_notifications:
            args.append("--push-notifications")
            args.extend(["--push-receiver", self.push_receiver])

        return args


def _packaged_resource_root() -> Path:
    """Return the packaged resource directory for bundled chat2 artifacts."""
    return Path(importlib.resources.files("aion.cli.bin"))


def _platform_binary_name() -> str:
    """Return the packaged binary name for the active platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system != "darwin":
        raise BinaryResolutionError(
            "chat2 currently ships macOS artifacts only. "
            f"Unsupported platform: {platform.system()}."
        )

    aliases = {
        "arm64": "darwin-arm64",
        "aarch64": "darwin-arm64",
        "x86_64": "darwin-x64",
        "amd64": "darwin-x64",
    }
    suffix = aliases.get(machine)
    if suffix is None:
        raise BinaryResolutionError(
            "chat2 does not have a packaged artifact for the current architecture. "
            f"Unsupported machine: {platform.machine()}."
        )

    return f"aion-chat-ui-{suffix}"


def _is_executable(path: Path) -> bool:
    """Return whether a path exists and is executable."""
    return path.exists() and os.access(path, os.X_OK)


def _repo_root_from_checkout() -> Optional[Path]:
    """Infer the repository root when running from a source checkout."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "libs" / "aion-chat-ui"
        if candidate.exists():
            return parent
    return None


def resolve_chat2_command() -> list[str]:
    """Resolve the best available command for running the standalone UI.

    Resolution order:
    1. Source-checkout bundle in ``libs/aion-chat-ui/dist/cli.mjs`` when ``node`` exists.
    2. Packaged macOS binary inside ``aion.cli.bin``.
    3. Packaged Node bundle ``cli.mjs`` inside ``aion.cli.bin`` when ``node`` exists.
    """

    node_binary = shutil.which("node")
    repo_root = _repo_root_from_checkout()
    if repo_root and node_binary:
        checkout_bundle = repo_root / "libs" / "aion-chat-ui" / "dist" / "cli.mjs"
        if checkout_bundle.exists():
            return [node_binary, str(checkout_bundle)]

    resource_root = _packaged_resource_root()
    try:
        binary_path = resource_root / _platform_binary_name()
    except BinaryResolutionError:
        binary_path = None

    if binary_path and _is_executable(binary_path):
        return [str(binary_path)]

    packaged_js_bundle = resource_root / "cli.mjs"
    if node_binary and packaged_js_bundle.exists():
        return [node_binary, str(packaged_js_bundle)]

    raise BinaryResolutionError(
        "Unable to locate the standalone chat2 artifact. "
        "Build and stage it with 'npm run prepare:python' from libs/aion-chat-ui."
    )


def launch_chat2(
    options: Chat2LaunchOptions,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    """Launch the standalone UI and return its exit code.

    Args:
        options: Launch options supplied by the Python CLI.
        runner: Process runner used for testability.

    Returns:
        Exit code from the standalone UI process.
    """
    command = [*resolve_chat2_command(), *options.to_args()]
    result = runner(command, check=False)
    return int(result.returncode)
