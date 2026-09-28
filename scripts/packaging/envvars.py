#!/usr/bin/env python3
"""List the environment variables this SDK reads, and where it reads them.

Two things make a grep unreliable here, and both of them have hidden a
variable before. A name can be passed to a helper as an argument, so it never
appears next to ``os.getenv`` at all; and pydantic-settings reads a name from
``Field(alias=...)`` or ``env_prefix``, where it is a model attribute rather
than a string. So this walks the syntax tree instead::

    scripts/packaging/envvars.py            every variable, with call sites
    scripts/packaging/envvars.py --names    just the names, one per line

What it finds:

* ``os.environ[...]``, ``os.environ.get(...)``, ``os.getenv(...)`` and
  ``environ.get(...)`` with a string literal.
* A string literal passed to a helper that reads the environment from its own
  parameter - ``_env_flag("EVOLUTION_EXECUTOR_NETWORK")`` and the like. The
  helpers are found first, by looking for a function whose body reaches
  ``os.environ`` or ``os.getenv`` through one of its parameters; then every
  call to one is read for the literal in that position. This is the case a
  grep cannot see at all, because the name never appears next to ``os``.
* A string literal passed as ``env_var=``, ``env=``, ``variable=`` or
  ``var_name=`` to anything.
* Module-level constants whose name ends in ``_ENV_VAR`` or ``_ENV``, since
  those exist to be passed somewhere else.
* pydantic-settings models: every ``Field(alias=...)``/``validation_alias``
  literal, and, when the model declares ``env_prefix``, the prefixed upper-case
  form of each field name. Recognised by base-class name, following the
  project's own ``BaseEnvSettings`` through to its subclasses - a settings
  class two levels below ``BaseSettings`` reads the environment exactly as one
  directly below it does.

It reports what the source reads and nothing else. What each name is for is
kept beside the code that reads it.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

ENV_PARAM_NAMES = {"env_var", "env", "variable", "var_name"}

# pydantic-settings' own base, plus this project's shared subclass of it. A
# settings model here inherits from the latter, so looking only for the former
# would find none of them.
SETTINGS_BASES = {"BaseSettings", "BaseEnvSettings"}


def _env_reading_helpers(tree: ast.AST) -> dict[str, int]:
    """Functions that read the environment from a parameter, and which one.

    ``def _env_flag(name): return os.environ.get(name, "")`` takes the variable
    name from its caller, so the name is a literal at the call site and nowhere
    near ``os``. Finding the helper first is what makes those call sites
    readable.

    Args:
        tree: Parsed module.

    Returns:
        Helper name mapped to the position of the parameter it reads from.
    """
    helpers: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = [argument.arg for argument in node.args.args]
        if not parameters:
            continue
        for inner in ast.walk(node):
            name = None
            if isinstance(inner, ast.Subscript) and _is_environ(inner.value):
                name = inner.slice
            elif isinstance(inner, ast.Call):
                function = inner.func
                if isinstance(function, ast.Attribute) and function.attr in ("get", "getenv"):
                    if (_is_environ(function.value) or _is_os(function.value)) and inner.args:
                        name = inner.args[0]
            if isinstance(name, ast.Name) and name.id in parameters:
                helpers[node.name] = parameters.index(name.id)
                break
    return helpers


class EnvVisitor(ast.NodeVisitor):
    """Collect environment-variable names and the lines they are read on."""

    def __init__(self, path: Path, helpers: dict[str, int] | None = None) -> None:
        self.path = path
        self.helpers = helpers or {}
        self.found: dict[str, list[int]] = defaultdict(list)

    def _record(self, name: object, lineno: int) -> None:
        if isinstance(name, str) and name and name.replace("_", "").isalnum():
            self.found[name].append(lineno)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_environ(node.value) and isinstance(node.slice, ast.Constant):
            self._record(node.slice.value, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in ("get", "getenv"):
            if _is_environ(function.value) or _is_os(function.value):
                if node.args and isinstance(node.args[0], ast.Constant):
                    self._record(node.args[0].value, node.lineno)
        if isinstance(function, ast.Name) and function.id in self.helpers:
            position = self.helpers[function.id]
            if position < len(node.args) and isinstance(node.args[position], ast.Constant):
                self._record(node.args[position].value, node.lineno)
        for keyword in node.keywords:
            if keyword.arg in ENV_PARAM_NAMES and isinstance(keyword.value, ast.Constant):
                self._record(keyword.value.value, node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and (
                target.id.endswith("_ENV_VAR") or target.id.endswith("_ENV")
            ):
                if isinstance(node.value, ast.Constant):
                    self._record(node.value.value, node.lineno)
        self.generic_visit(node)


def _is_os(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "os"


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id == "environ":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "environ"


def _settings_aliases(tree: ast.AST) -> dict[str, int]:
    """Names a pydantic-settings model reads, which never appear as env strings."""
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        bases |= {b.attr for b in node.bases if isinstance(b, ast.Attribute)}
        if not bases & SETTINGS_BASES:
            continue

        prefix = ""
        for statement in ast.walk(node):
            if isinstance(statement, ast.Call):
                for keyword in statement.keywords:
                    if keyword.arg == "env_prefix" and isinstance(keyword.value, ast.Constant):
                        prefix = str(keyword.value.value)
                    if keyword.arg in ("alias", "validation_alias") and isinstance(
                        keyword.value, ast.Constant
                    ):
                        found[str(keyword.value.value)] = statement.lineno

        if prefix:
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    found.setdefault(
                        f"{prefix}{statement.target.id}".upper(), statement.lineno
                    )
    return found


def collect() -> dict[str, list[str]]:
    """Every variable name mapped to the ``path:line`` sites that read it."""
    sites: dict[str, list[str]] = defaultdict(list)
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = EnvVisitor(path, _env_reading_helpers(tree))
        visitor.visit(tree)
        relative = path.relative_to(PROJECT_ROOT)
        for name, lines in visitor.found.items():
            for line in sorted(set(lines)):
                sites[name].append(f"{relative}:{line}")
        for name, line in _settings_aliases(tree).items():
            sites[name].append(f"{relative}:{line}")
    return dict(sites)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--names", action="store_true", help="Print names only")
    arguments = parser.parse_args(argv)

    sites = collect()
    for name in sorted(sites):
        if arguments.names:
            print(name)
        else:
            print(f"{name}")
            for site in sorted(set(sites[name])):
                print(f"    {site}")
    if not arguments.names:
        print(f"\n{len(sites)} environment variables read under src/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
