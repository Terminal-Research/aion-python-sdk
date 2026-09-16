#!/usr/bin/env python3
"""Write the scenario matrix: every scenario, what it drives, and where it runs.

The document is built from the sources of truth and nothing else - the
collected tests, ``commands.COMMANDS``, ``frameworks.FRAMEWORKS`` with
``UNSUPPORTED``, and each agent's routing table - so it cannot say something
the suite does not. Collection is all pytest does here: no server starts, and
a run takes about a second.

    scripts/scenarios_matrix.py            rewrite tests/scenarios/SCENARIOS.md
    scripts/scenarios_matrix.py --check    exit 1 when that file is stale

``make scenarios-matrix`` is the first; CI runs the second.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import inspect
import io
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.scenarios.commands import COMMANDS  # noqa: E402
from tests.scenarios.frameworks import (  # noqa: E402
    FRAMEWORKS,
    UNSUPPORTED,
    Framework,
    implemented_commands,
    unsupported_reason,
)

TESTS = ROOT / "tests" / "scenarios"
OUTPUT = TESTS / "SCENARIOS.md"
"""Beside the suite it describes, so its links are the files next to it."""

DEFAULT_VARIANT = "default"

SUITE_PREFIX = "scenario suite -"
"""What marks a suite marker in pyproject.toml, among all the others."""

# Suites the plain `make tests-scenarios` leaves out, and the target that runs each
# instead. Mirrors the default of SCENARIO_TAGS in the Makefile.
SUITE_TARGETS = {
    "persistence": "make tests-scenarios-pg",
}

# One vocabulary for every status cell in the document, in both tables. Short
# enough to scan down a column, and explained once under the title.
RUNS = "\u2713"
SKIP = "skip"
GAP = "gap"
ANY_FRAMEWORK = "any"
NOT_PARAMETRIZED = "n/a"
NOTHING = "\u2014"

FRAMEWORKS_ANCHOR = "#frameworks"
"""Where a `skip` cell sends the reader for the reason."""


# --------------------------------------------------------------------------
# What a scenario is, once collected
# --------------------------------------------------------------------------

@dataclass
class Scenario:
    """One test function, with the runs pytest would make of it."""

    file: str
    line: int
    name: str
    checks: str
    suites: tuple[str, ...]
    command: str | None
    variant: str
    xfail: str | None
    frameworks: list[str] = field(default_factory=list)

    @property
    def node_id(self) -> str:
        """The id to hand to pytest, before parametrization."""
        return f"{self.file}::{self.name}"

    def status(self, framework: Framework) -> str:
        """What happens to this scenario on that framework, as one token.

        The reason behind a ``skip`` is not repeated here: it is one row of
        the UNSUPPORTED table, which every skipped cell links to. An
        ``xfail`` keeps its reason, because that one is about this scenario
        and nothing else names it.
        """
        if self.frameworks and framework.name not in self.frameworks:
            return NOT_PARAMETRIZED
        if self.command is not None:
            if unsupported_reason(framework.name, self.command):
                return SKIP
            if self.command not in implemented_commands(framework):
                return GAP
        if self.xfail:
            return f"xfail: {self.xfail}"
        return RUNS


@dataclass
class Suite:
    """One suite marker from pyproject.toml."""

    name: str
    description: str

    @property
    def target(self) -> str:
        """The make invocation that runs this suite alone."""
        return SUITE_TARGETS.get(self.name, f"make tests-scenarios TAGS={self.name}")


class _Collector:
    """Pytest plugin that keeps the collected items."""

    def __init__(self) -> None:
        self.items: list[pytest.Item] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.items = list(session.items)


def load_suites() -> list[Suite]:
    """The suite markers pyproject.toml declares, in its order.

    A suite is a marker whose description starts with ``scenario suite -``;
    that is what tells the thirteen of them from ``integration``, ``scenario``
    and the two that carry an argument.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        markers = tomllib.load(handle)["tool"]["pytest"]["ini_options"]["markers"]
    suites = []
    for line in markers:
        name, _, description = line.partition(":")
        description = description.strip()
        if not description.startswith(SUITE_PREFIX):
            continue
        suites.append(Suite(name.strip(), description[len(SUITE_PREFIX):].strip()))
    return suites


def collect(suite_names: set[str]) -> list[Scenario]:
    """Collect the scenarios without running any, folded by test function."""
    collector = _Collector()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = pytest.main(
            ["--collect-only", "-q", "-p", "no:cacheprovider", "--rootdir", str(ROOT), str(TESTS)],
            plugins=[collector],
        )
    if code != pytest.ExitCode.OK:
        sys.stderr.write(output.getvalue())
        raise SystemExit(f"collection failed with {code!r}")

    scenarios: dict[str, Scenario] = {}
    for item in collector.items:
        file, line, _ = item.location
        base_id = item.nodeid.split("[", 1)[0]
        scenario = scenarios.get(base_id)
        if scenario is None:
            command = item.get_closest_marker("command")
            variant = item.get_closest_marker("variant")
            xfail = item.get_closest_marker("xfail")
            doc = inspect.getdoc(getattr(item, "obj", None)) or ""
            scenario = Scenario(
                file=file,
                line=line + 1,
                name=item.originalname if isinstance(item, pytest.Function) else item.name,
                checks=doc.splitlines()[0] if doc else "",
                suites=tuple(m.name for m in item.iter_markers() if m.name in suite_names),
                command=command.args[0] if command else None,
                variant=variant.args[0] if variant else DEFAULT_VARIANT,
                xfail=str(xfail.kwargs.get("reason", "")) if xfail else None,
            )
            scenarios[base_id] = scenario
        callspec = getattr(item, "callspec", None)
        if callspec is not None and "framework" in callspec.params:
            scenario.frameworks.append(callspec.params["framework"])
    return list(scenarios.values())


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def cell(text: str) -> str:
    """Text safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def code(text: str) -> str:
    """Text as inline code, so `<n>` and the like survive rendering."""
    return f"`{text}`" if text else ""


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    """A Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return lines


def _totals(scenarios: list[Scenario], frameworks: list[Framework]) -> str:
    """The one line that says how much there is and how much of it runs."""
    counted: dict[str, int] = defaultdict(int)
    for scenario in scenarios:
        if not scenario.frameworks:
            counted[ANY_FRAMEWORK] += 1
            continue
        for framework in frameworks:
            if framework.name in scenario.frameworks:
                counted[scenario.status(framework)] += 1

    wording = (
        (RUNS, "run"),
        (SKIP, "skipped"),
        (GAP, "waiting for a behaviour"),
        (ANY_FRAMEWORK, "independent of the framework"),
        (NOT_PARAMETRIZED, "not parametrized"),
    )
    parts = [f"{counted[key]} {word}" for key, word in wording if counted.get(key)]
    deferred = sum(count for key, count in counted.items() if key.startswith("xfail"))
    if deferred:
        parts.append(f"{deferred} expected to fail")

    files = len({scenario.file for scenario in scenarios})
    return (
        f"{len(scenarios)} scenarios in {files} files, {sum(counted.values())} runs across "
        f"{len(frameworks)} framework{'s' if len(frameworks) != 1 else ''}: {', '.join(parts)}."
    )


def _coverage(scenarios: list[Scenario], suites: list[Suite], driven: set[str]) -> list[str]:
    """What the suite reaches and what it does not, in one table.

    The rest of the document answers this too, in two tables a reader has to
    cross-check; this is the answer they came for.
    """
    covered_suites = [suite for suite in suites if any(suite.name in s.suites for s in scenarios)]
    empty_suites = [suite for suite in suites if suite not in covered_suites]
    commands = [command.key for command in COMMANDS]

    def listed(keys: list[str]) -> str:
        return ", ".join(code(key) for key in keys) or NOTHING

    return table(
        ["", "Covered", "Not yet"],
        [
            [
                "Commands",
                f"{len(driven)} of {len(commands)}: " + listed([k for k in commands if k in driven]),
                listed([key for key in commands if key not in driven]),
            ],
            [
                "Suites",
                f"{len(covered_suites)} of {len(suites)}: " + listed([s.name for s in covered_suites]),
                listed([suite.name for suite in empty_suites]),
            ],
        ],
    )


def _scenario_cell(scenario: Scenario) -> str:
    """A scenario as a row label: what it checks, linked to the line it is on.

    The sentence is the label rather than the function name - the name says
    the same thing in snake_case and twice as wide - and the name is the
    link's title, so hovering gives the argument for ``-k``.
    """
    label = scenario.checks or _pretty(scenario.name)
    return f'[{label}]({_link(scenario.file)}#L{scenario.line} "{scenario.name}")'


def _pretty(name: str) -> str:
    """A test function's name as a sentence, for a scenario with no docstring."""
    words = name.removeprefix("test_").replace("_", " ")
    return words[:1].upper() + words[1:]


def _status_cell(status: str) -> str:
    """One status, with `skip` pointing at the reason instead of repeating it."""
    return f"[{SKIP}]({FRAMEWORKS_ANCHOR})" if status == SKIP else status


def render(scenarios: list[Scenario], suites: list[Suite]) -> str:
    """The whole of the document."""
    frameworks = list(FRAMEWORKS)
    by_file: dict[str, list[Scenario]] = defaultdict(list)
    for scenario in scenarios:
        by_file[scenario.file].append(scenario)
    by_command: dict[str, list[Scenario]] = defaultdict(list)
    for scenario in scenarios:
        if scenario.command:
            by_command[scenario.command].append(scenario)

    lines = [
        "# Scenario matrix",
        "",
        "<!-- Generated by scripts/scenarios_matrix.py from the collected scenarios,",
        "     commands.py and frameworks.py. Do not edit by hand: run",
        "     `make scenarios-matrix`. -->",
        "",
        _totals(scenarios, frameworks),
        "",
        "Nothing here was produced by running a scenario: `pytest --collect-only` and the "
        "registries are all it takes, and the same suite always renders the same file. What "
        "the suite is and how to run it is in [README.md](README.md).",
        "",
        f"A status cell reads `{RUNS}` when it runs, `{SKIP}` when the pair is one "
        f"`frameworks.UNSUPPORTED` names (the reason is under "
        f"[Frameworks]({FRAMEWORKS_ANCHOR})), `{GAP}` when the agent has no behaviour for the "
        f"command yet, `xfail: <reason>` for a knowingly deferred defect, "
        f"`{ANY_FRAMEWORK}` when the scenario does not depend on a framework, and "
        f"`{NOT_PARAMETRIZED}` when it does not run on that one.",
        "",
        "## Coverage at a glance",
        "",
    ]
    lines += _coverage(scenarios, suites, set(by_command))

    lines += ["", "## Frameworks", ""]
    lines += table(
        ["Framework", "Agent package", "Entry", "SDK extras", "Commands implemented"],
        [
            [
                framework.name,
                code(framework.agent_package),
                code(framework.agent_entry),
                ", ".join(code(extra) for extra in framework.sdk_extras),
                f"{len(implemented_commands(framework))} of {len(COMMANDS)}",
            ]
            for framework in frameworks
        ],
    )
    if UNSUPPORTED:
        lines += ["", "Pairs a framework genuinely cannot do, which is what a `skip` cell means:", ""]
        lines += table(
            ["Framework", "Command", "Reason"],
            [[framework, code(command), reason] for (framework, command), reason in UNSUPPORTED.items()],
        )

    lines += ["", "## Suites", "", "One marker per suite, from `pyproject.toml`; `TAGS=` selects on them.", ""]
    lines += table(
        ["Suite", "What it covers", "Scenarios", "Run"],
        [
            [
                code(suite.name),
                suite.description,
                str(sum(1 for scenario in scenarios if suite.name in scenario.suites)),
                code(suite.target),
            ]
            for suite in suites
        ],
    )

    lines += ["", "## Scenarios by file", ""]
    for file, items in by_file.items():
        module_doc = inspect.getdoc(sys.modules.get(_module_name(file))) or ""
        lines += [f"### `{file}`", ""]
        if module_doc:
            lines += [module_doc.splitlines()[0], ""]
        lines += table(
            ["Scenario", "Suite", "Command", "Deployment"] + [fw.name for fw in frameworks],
            [
                [
                    _scenario_cell(scenario),
                    ", ".join(code(suite) for suite in scenario.suites) or NOTHING,
                    code(scenario.command) if scenario.command else NOTHING,
                    code(scenario.variant),
                ]
                + [
                    _status_cell(scenario.status(framework) if scenario.frameworks else ANY_FRAMEWORK)
                    for framework in frameworks
                ]
                for scenario in items
            ],
        )
        lines.append("")

    lines += [
        "## Commands",
        "",
        "The contract from `commands.py`. `Scenarios` counts the scenarios driving the command; "
        f"a framework column says whether that agent answers it - `{RUNS}` for a behaviour it "
        f"has, `{GAP}` for `not implemented: <key>`, which is what a scenario would receive.",
        "",
    ]
    lines += table(
        ["Command", "Summary", "Tags", "Scenarios"] + [fw.name for fw in frameworks],
        [
            [
                code(command.usage),
                command.summary,
                ", ".join(code(tag) for tag in command.tags),
                str(len(by_command.get(command.key, []))),
            ]
            + [_command_status(framework, command.key) for framework in frameworks]
            for command in COMMANDS
        ],
    )
    lines.append("")
    return "\n".join(lines)


def _command_status(framework: Framework, key: str) -> str:
    """Whether the framework's agent answers this command."""
    if unsupported_reason(framework.name, key):
        return _status_cell(SKIP)
    return RUNS if key in implemented_commands(framework) else GAP


def _link(file: str) -> str:
    """The collected file's path, relative to the document beside it.

    The heading keeps the path from the repository root - it is what gets
    typed after ``pytest`` - and the link is the same file reached from here.
    """
    return file.removeprefix(f"{TESTS.relative_to(ROOT)}/")


def _module_name(file: str) -> str:
    """The import name pytest gave a collected test file.

    Every directory under ``tests/`` carries an ``__init__.py``, so the module
    is imported under its full dotted path and that is what the file's
    position spells out.
    """
    return file.removesuffix(".py").replace("/", ".")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Write the matrix, or check it against what would be written."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when the matrix is out of date")
    args = parser.parse_args(argv)

    suites = load_suites()
    rendered = render(collect({suite.name for suite in suites}), suites)
    relative = OUTPUT.relative_to(ROOT)

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current == rendered:
            print(f"{relative} is up to date")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{relative} (committed)",
            tofile=f"{relative} (generated)",
        )
        sys.stdout.writelines(diff)
        print(f"\n{relative} is stale: run `make scenarios-matrix` and commit the result", file=sys.stderr)
        return 1

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
