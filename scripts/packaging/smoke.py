#!/usr/bin/env python3
"""Install the built distributions into clean virtual environments and use them.

``check.py`` reads the artifacts; this runs them. Everything here is done with
a fresh ``venv`` and ``pip install`` against ``dist/``, so what is exercised is
the installation a user gets - not the working tree, and not the development
environment, both of which have every library present and would hide the one
property the extras exist for.

The six environments below are one table in the plan and one idea: each extra
has to bring exactly what it advertises, and a base install has to stay usable
without any of them. So the negative checks matter as much as the positive
ones - a base install that quietly has ``fastapi`` in it proves nothing.

Nothing here starts a server or runs an agent. The line between "the package
installs and its entry points work" and "the product works" is the line
between this script and the test suite.

Exit status is 0 when every environment passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The venv layout differs on Windows; the rest of the tooling is POSIX-only,
# but getting this one detail wrong would be a confusing way to find out.
BIN_DIR = "Scripts" if os.name == "nt" else "bin"

VERSION_ARGUMENT = re.compile(r"^\d+\.\d+$")


def probe_imports(*modules: str) -> str:
    """Code that imports each module and says so."""
    lines = [f"import {module}" for module in modules]
    lines.append(f"print('imported', {', '.join(repr(m) for m in modules)})")
    return "\n".join(lines)


def probe_needs_extra(module: str, extra: str) -> str:
    """Code asserting that importing ``module`` asks for ``extra`` by name.

    The failure this replaces is an ImportError from somewhere inside a
    third-party package, naming a module the person who wrote the agent has
    never heard of.
    """
    return f"""
from aion.core.utils.optional_deps import MissingOptionalDependency

try:
    import {module}
except MissingOptionalDependency as exc:
    hint = 'pip install "aionto-sdk[{extra}]"'
    assert hint in str(exc), str(exc)
    print('asks for [{extra}]')
else:
    raise AssertionError('{module} imported in an installation without [{extra}]')
"""


def probe_absent(*modules: str) -> str:
    """Code asserting that none of ``modules`` can be imported.

    Written with find_spec rather than an import: several of these packages do
    real work at import time, and the question here is only whether the extra
    put them in the environment.
    """
    return f"""
import importlib.util

wanted_absent = {list(modules)!r}
present = []
for name in wanted_absent:
    try:
        found = importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        # A missing parent package - "google" for "google.adk".
        found = False
    if found:
        present.append(name)

assert not present, 'installed but should not be: ' + repr(present)
print('absent:', wanted_absent)
"""


def probe_discovery(loaded: tuple[str, ...], skipped: tuple[str, ...]) -> str:
    """Code checking which framework plugins this installation can load.

    Discovery is asked directly, and then the message an operator would
    actually meet is built from what discovery recorded: a skipped plugin is
    only useful if the reason survives to the point where an agent cannot be
    built.
    """
    return f"""
import asyncio

from aion.server.agent.exceptions import NoAdapterFoundError
from aion.server.plugins.factory import PluginFactory
from aion.server.plugins.registry import PluginRegistry

expected_loaded = {sorted(loaded)!r}
expected_skipped = {sorted(skipped)!r}

# The private method, as the discovery tests call it: initialize() would go on
# to set the plugins up, which needs a database.
registry = PluginRegistry()
plugins = asyncio.run(PluginFactory(registry=registry)._discover_plugins())

names = sorted(type(plugin).__name__ for plugin in plugins)
assert names == expected_loaded, 'loaded ' + repr(names) + ', expected ' + repr(expected_loaded)

skipped = registry.get_skipped()
extras = sorted(entry.extra for entry in skipped)
assert extras == expected_skipped, 'skipped ' + repr(extras) + ', expected ' + repr(expected_skipped)

message = str(
    NoAdapterFoundError(
        agent_id='smoke',
        module_path='smoke.agent',
        available_frameworks=[],
        skipped_plugins=skipped,
    )
)
for extra in expected_skipped:
    hint = 'pip install "aionto-sdk[' + extra + ']"'
    assert hint in message, message

print('loaded', names, 'skipped', extras)
"""


@dataclass(frozen=True)
class Step:
    """One thing to do inside a prepared environment."""

    label: str
    code: str | None = None
    """Python source run by the environment's own interpreter."""

    argv: tuple[str, ...] | None = None
    """A command from the environment's bin/ directory, run instead."""


@dataclass(frozen=True)
class Environment:
    """One installation to build and use."""

    name: str
    artifact: str
    """"wheel" or "sdist" - which of the two built files is installed."""

    extras: tuple[str, ...] = ()
    steps: tuple[Step, ...] = field(default_factory=tuple)

    def requirement(self, artifact_path: Path) -> str:
        """The argument to pip: a local file, with extras appended."""
        if not self.extras:
            return str(artifact_path)
        return f"{artifact_path}[{','.join(self.extras)}]"


CONTRACT_BASE_MODULES = ("aion.core", "aion.api", "aion.mcp")

ENVIRONMENTS = (
    Environment(
        name="base",
        artifact="wheel",
        steps=(
            Step("the base subpackages import", code=probe_imports(*CONTRACT_BASE_MODULES)),
            Step("aion --help", argv=("aion", "--help")),
            Step(
                "aion.langgraph.authoring names its extra",
                code=probe_needs_extra("aion.langgraph.authoring", "langgraph-authoring"),
            ),
            Step(
                "aion.adk.authoring names its extra",
                code=probe_needs_extra("aion.adk.authoring", "adk-authoring"),
            ),
            Step(
                "no framework or server libraries",
                code=probe_absent("langgraph", "google.adk", "fastapi"),
            ),
        ),
    ),
    Environment(
        name="server",
        artifact="wheel",
        extras=("server",),
        steps=(
            Step(
                "the server subpackages import",
                code=probe_imports("aion.server", "aion.proxy", "aion.db.postgres"),
            ),
            Step("aion serve --help", argv=("aion", "serve", "--help")),
            Step(
                "discovery skips both frameworks with hints",
                code=probe_discovery(loaded=(), skipped=("langgraph-server", "adk-server")),
            ),
            Step(
                "no framework libraries",
                code=probe_absent("langgraph", "google.adk"),
            ),
        ),
    ),
    Environment(
        name="langgraph-server",
        artifact="wheel",
        extras=("langgraph-server",),
        steps=(
            Step(
                "the LangGraph subpackages import",
                code=probe_imports(
                    "aion.langgraph.authoring", "aion.langgraph.server", "aion.server"
                ),
            ),
            Step(
                "discovery loads LangGraph and skips ADK with a hint",
                code=probe_discovery(loaded=("LangGraphPlugin",), skipped=("adk-server",)),
            ),
            Step("no ADK libraries", code=probe_absent("google.adk")),
        ),
    ),
    Environment(
        name="adk-server",
        artifact="wheel",
        extras=("adk-server",),
        steps=(
            Step(
                "the ADK subpackages import",
                code=probe_imports("aion.adk.authoring", "aion.adk.server", "aion.server"),
            ),
            Step(
                "discovery loads ADK and skips LangGraph with a hint",
                code=probe_discovery(loaded=("ADKPlugin",), skipped=("langgraph-server",)),
            ),
            Step("no LangGraph libraries", code=probe_absent("langgraph")),
        ),
    ),
    # Both framework extras at once: the resolver has to accept the union, and
    # there is no [all] extra to name it - the install line names both.
    Environment(
        name="both",
        artifact="wheel",
        extras=("langgraph-server", "adk-server"),
        steps=(
            Step(
                "every subpackage imports",
                code=probe_imports(
                    "aion.core",
                    "aion.api",
                    "aion.db",
                    "aion.mcp",
                    "aion.server",
                    "aion.proxy",
                    "aion.cli",
                    "aion.langgraph.authoring",
                    "aion.langgraph.server",
                    "aion.adk.authoring",
                    "aion.adk.server",
                ),
            ),
            Step(
                "discovery loads both frameworks",
                code=probe_discovery(loaded=("ADKPlugin", "LangGraphPlugin"), skipped=()),
            ),
        ),
    ),
    Environment(
        name="sdist",
        artifact="sdist",
        steps=(
            Step("the base subpackages import", code=probe_imports(*CONTRACT_BASE_MODULES)),
            Step("aion --help", argv=("aion", "--help")),
        ),
    ),
)


class Report:
    """Collected outcomes, printed once at the end."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.lines: list[str] = []

    def ok(self, message: str) -> None:
        self.lines.append(f"  ok    {message}")

    def skip(self, message: str) -> None:
        self.lines.append(f"  skip  {message}")

    def fail(self, message: str) -> None:
        self.lines.append(f"  FAIL  {message}")
        self.failures.append(message)

    def section(self, title: str) -> None:
        self.lines.append(f"\n{title}")


def indent(text: str, prefix: str = "        ") -> str:
    return "\n".join(prefix + line for line in text.strip().splitlines())


def resolve_python(requested: str | None) -> str:
    """Turn ``--python`` into an interpreter to build venvs with.

    Accepts a bare version ("3.12"), a command name ("python3.12") or a path.
    Defaults to the interpreter running this script, which under
    ``make dist-smoke`` is the project's own - fine for a single-version check
    and never enough for a release, which is why CI passes the versions.
    """
    if requested is None:
        return sys.executable
    candidate = f"python{requested}" if VERSION_ARGUMENT.match(requested) else requested
    found = shutil.which(candidate)
    if found is None and Path(candidate).is_file():
        found = str(Path(candidate).resolve())
    if found is None:
        raise SystemExit(f"no such interpreter: {candidate}")
    return found


def find_artifacts(dist_dir: Path) -> dict[str, Path]:
    """The one wheel and the one sdist, as check.py insists there be."""
    artifacts: dict[str, Path] = {}
    for kind, pattern in (("wheel", "*.whl"), ("sdist", "*.tar.gz")):
        found = sorted(dist_dir.glob(pattern))
        if len(found) != 1:
            raise SystemExit(
                f"expected exactly one {kind} in {dist_dir}, found {len(found)}: "
                f"{[p.name for p in found]} - run `make dist-build`"
            )
        artifacts[kind] = found[0]
    return artifacts


def run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def create_environment(python: str, root: Path, environment: Environment, artifact: Path) -> Path:
    """Build the venv and install into it; returns the venv's interpreter.

    The install is the only step allowed to reach the network, and it is
    pointed at a file: no index entry for aionto-sdk exists yet, and once one
    does, this must still test the artifact beside it rather than the last
    release.
    """
    venv_dir = root / environment.name
    created = run([python, "-m", "venv", str(venv_dir)])
    if created.returncode != 0:
        raise RuntimeError(f"could not create a venv:\n{created.stdout}{created.stderr}")

    venv_python = venv_dir / BIN_DIR / "python"
    installed = run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            environment.requirement(artifact),
        ]
    )
    if installed.returncode != 0:
        raise RuntimeError(f"pip install failed:\n{installed.stdout}{installed.stderr}")
    return venv_python


def check_environment(
    python: str,
    root: Path,
    environment: Environment,
    artifacts: dict[str, Path],
    report: Report,
) -> None:
    artifact = artifacts[environment.artifact]
    extras = f"[{','.join(environment.extras)}]" if environment.extras else ""
    report.section(f"{environment.name}: {artifact.name}{extras}")

    started = time.monotonic()
    try:
        venv_python = create_environment(python, root, environment, artifact)
    except RuntimeError as exc:
        report.fail(f"{environment.name}: installation failed")
        report.lines.append(indent(str(exc)))
        return
    report.ok(f"installed in {time.monotonic() - started:.0f}s")

    # A conflict between the extras is a packaging bug even when every import
    # below happens to work: pip resolves what it can and reports the rest.
    consistent = run([str(venv_python), "-m", "pip", "check"])
    if consistent.returncode == 0:
        report.ok("pip check: dependencies are consistent")
    else:
        report.fail(f"{environment.name}: pip check found conflicts")
        report.lines.append(indent(consistent.stdout + consistent.stderr))

    for step in environment.steps:
        if step.argv is not None:
            argv = [str(venv_python.parent / step.argv[0]), *step.argv[1:]]
        else:
            argv = [str(venv_python), "-c", step.code or ""]
        result = run(argv)
        if result.returncode == 0:
            report.ok(step.label)
        else:
            report.fail(f"{environment.name}: {step.label}")
            report.lines.append(indent(result.stdout + result.stderr))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        type=Path,
        default=REPO_ROOT / "dist",
        help="directory holding the built distributions (default: ./dist)",
    )
    parser.add_argument(
        "--python",
        help="interpreter to build the environments with: 3.12, python3.12 or a path "
        "(default: the interpreter running this script)",
    )
    parser.add_argument(
        "--only",
        help="comma-separated environment names to run "
        f"(default: all of {', '.join(e.name for e in ENVIRONMENTS)})",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the environments behind for inspection",
    )
    args = parser.parse_args()

    if not args.dist.is_dir():
        print(f"{args.dist} does not exist: run `make dist-build` first", file=sys.stderr)
        return 1

    known = {environment.name: environment for environment in ENVIRONMENTS}
    if args.only:
        wanted = [name.strip() for name in args.only.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in known]
        if unknown:
            print(f"no such environment: {unknown}", file=sys.stderr)
            return 1
        selected = [known[name] for name in wanted]
    else:
        selected = list(ENVIRONMENTS)

    python = resolve_python(args.python)
    artifacts = find_artifacts(args.dist)

    version = run([python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    print(f"smoke testing {args.dist} with {python} ({version.stdout.strip()})")

    report = Report()
    root = Path(tempfile.mkdtemp(prefix="aion-smoke-"))
    try:
        for environment in selected:
            check_environment(python, root, environment, artifacts, report)
    finally:
        if args.keep:
            report.lines.append(f"\nenvironments left in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    skipped = [e.name for e in ENVIRONMENTS if e not in selected]
    print("\n".join(report.lines))
    if skipped:
        print(f"\nnot run: {', '.join(skipped)}")
    if report.failures:
        print(f"\n{len(report.failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
