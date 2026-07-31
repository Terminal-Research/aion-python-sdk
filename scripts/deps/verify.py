#!/usr/bin/env python3
"""
Verify that each environment resolves its sibling packages as declared.

Poetry decides whether to reinstall by comparing name, version and source, and
does not track whether the installed distribution is editable. An environment
can therefore hold a non-editable copy of a sibling while `poetry install`,
`poetry sync` and `poetry lock --regenerate` all report that there is nothing to
do. Edits to the source library then silently fail to take effect. This script
detects that state by reading each installed distribution's PEP 610
`direct_url.json` and comparing it against what `pyproject.toml` declares.

It also reports leftover `.venv/src/` clones. While dependencies were git
references, Poetry cloned the whole monorepo into each environment. Those clones
are unused once dependencies are local paths, but they stay on disk frozen at
the commit they were cloned from — browsing them looks exactly like a broken
editable install.

Usage:
    python verify.py            # report only, non-zero exit if anything is wrong
    python verify.py --clean    # additionally delete leftover .venv/src clones

Example:
    $ python scripts/deps/verify.py
    [PACKAGE] aion-server
      [OK] aion-core        editable < libs/aion-core
      [OK] aion-api-client  editable < libs/aion-api-client
    [COMPLETE] 10 packages checked, all consistent
"""

import json
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Dict, List, Tuple

from config import find_pyproject_files, ROOT_DIR
from package_ops import check_poetry_available, run_command, validate_libs_dir

# Executed inside each package environment. Reads PEP 610 metadata for every
# requested distribution and reports how it was installed.
PROBE = r'''
import json, sys
from importlib.metadata import Distribution, PackageNotFoundError

result = {}
for name in sys.argv[1:]:
    try:
        dist = Distribution.from_name(name)
    except PackageNotFoundError:
        result[name] = {"state": "missing", "url": ""}
        continue

    raw = dist.read_text("direct_url.json")
    if not raw:
        result[name] = {"state": "published", "url": ""}
        continue

    info = json.loads(raw)
    editable = info.get("dir_info", {}).get("editable", False)
    result[name] = {
        "state": "editable" if editable else "copy",
        "url": info.get("url", ""),
    }

print(json.dumps(result))
'''


def read_sibling_dependencies(pyproject: Path) -> Dict[str, dict]:
    """
    Collect the `aion-*` dependencies a package declares and how.

    Optional dependencies are recorded as such: they are exposed through extras
    and are not installed by a plain `poetry install`, so their absence is the
    expected state rather than a fault.

    Args:
        pyproject: Path to the package's `pyproject.toml`

    Returns:
        Mapping of dependency name to a dict with two keys — ``kind``, either
        ``"local"`` for a path dependency or ``"remote"`` for a git reference,
        and ``optional``, whether the dependency sits behind an extra
    """
    try:
        with pyproject.open('rb') as handle:
            data = tomllib.load(handle)
    except Exception as ex:
        print(f"  [WARNING] Failed to parse TOML: {ex}")
        return {}

    dependencies = data.get('tool', {}).get('poetry', {}).get('dependencies', {})

    expected = {}
    for name, config in dependencies.items():
        if not name.startswith('aion-') or not isinstance(config, dict):
            continue

        if 'path' in config:
            kind = 'local'
        elif 'git' in config:
            kind = 'remote'
        else:
            continue

        expected[name] = {'kind': kind, 'optional': config.get('optional', False)}

    return expected


def probe_environment(package_dir: Path, dep_names: List[str]) -> Dict[str, dict]:
    """
    Ask a package's environment how each dependency is installed.

    Args:
        package_dir: Directory of the package whose environment is inspected
        dep_names: Distribution names to look up

    Returns:
        Mapping of distribution name to its probe result, empty if the
        environment could not be queried
    """
    command = ['poetry', 'run', 'python', '-c', PROBE, *dep_names]
    success, stdout, stderr = run_command(command, package_dir)

    if not success:
        print(f"  [ERROR] Could not query environment")
        for stream in (stdout, stderr):
            if stream and stream.strip():
                print(f"  {stream.strip()}")
        return {}

    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"  [ERROR] Unexpected probe output: {stdout.strip()[:200]}")
        return {}


def describe(path_url: str) -> str:
    """
    Render a `file://` URL relative to the repository root for readability.

    Args:
        path_url: Value of the `url` field from `direct_url.json`

    Returns:
        Repository-relative path, or the original string when it is not a
        local file URL
    """
    if not path_url.startswith('file://'):
        return path_url

    path = Path(path_url[len('file://'):])
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def check_package(pyproject: Path) -> Tuple[int, int]:
    """
    Verify a single package and print its report.

    Args:
        pyproject: Path to the package's `pyproject.toml`

    Returns:
        Tuple of (number of dependencies checked, number of problems found)
    """
    package_dir = pyproject.parent
    expected = read_sibling_dependencies(pyproject)

    if not expected:
        return 0, 0

    print(f"\n[PACKAGE] {package_dir.name}")

    if not (package_dir / '.venv').exists():
        print("  [SKIP] no environment (.venv not found)")
        return 0, 0

    actual = probe_environment(package_dir, sorted(expected))
    if not actual:
        return 0, 1

    problems = 0
    for name in sorted(expected):
        want = expected[name]['kind']
        optional = expected[name]['optional']
        state = actual.get(name, {}).get('state', 'missing')
        source = describe(actual.get(name, {}).get('url', ''))

        if want == 'local' and state == 'editable':
            print(f"  [OK] {name:<26} editable < {source}")
        elif want == 'remote' and state in ('copy', 'published'):
            print(f"  [OK] {name:<26} installed from git")
        elif state == 'missing' and optional:
            print(f"  [EXTRA] {name:<23} not installed (optional, behind an extra)")
        elif want == 'local' and state == 'copy':
            print(f"  [STALE] {name:<23} non-editable copy — edits to {name} are ignored")
            print(f"          fix: cd libs/{package_dir.name} && "
                  f"poetry run pip install -e ../{name} --no-deps")
            problems += 1
        elif want == 'remote' and state == 'editable':
            print(f"  [LEFTOVER] {name:<20} editable from local mode, but declared as git")
            print(f"          fix: cd libs/{package_dir.name} && poetry sync")
            problems += 1
        elif state == 'missing':
            print(f"  [MISSING] {name:<21} not installed — run make deps-install")
            problems += 1
        else:
            print(f"  [UNEXPECTED] {name:<18} declared {want}, installed as {state}")
            problems += 1

    return len(expected), problems


def handle_stale_clones(clean: bool, local_mode: bool) -> int:
    """
    Report, and optionally delete, `.venv/src` monorepo clones.

    Poetry clones the monorepo into an environment when it installs a git
    dependency. Under git dependencies those clones are the live build source
    and must be left alone. Once dependencies point at local paths, nothing
    reads them any more, yet they remain on disk frozen at the commit they were
    cloned from — browsing one looks exactly like an editable install that
    stopped picking up edits.

    Args:
        clean: Delete the clones instead of only reporting them
        local_mode: Whether the monorepo currently declares local path dependencies

    Returns:
        Number of clones found
    """
    clones = sorted(path for path in ROOT_DIR.glob('libs/*/.venv/src') if path.is_dir())

    if not clones:
        return 0

    if not local_mode:
        print(f"\n[CLONES] {len(clones)} monorepo clone(s) in use by git dependencies — left alone")
        return len(clones)

    print(f"\n[STALE CLONES] {len(clones)} leftover monorepo clone(s) from git dependencies")
    for path in clones:
        relative = path.relative_to(ROOT_DIR)
        if clean:
            shutil.rmtree(path)
            print(f"  [REMOVED] {relative}")
        else:
            print(f"  {relative}")

    if not clean:
        print("  These are unused but look like real sources when browsed.")
        print("  Remove them with: make deps-verify-clean")

    return len(clones)


def main() -> int:
    """
    Main entry point for the verify script.

    Returns:
        0 when every environment matches its declaration, 1 otherwise
    """
    if not validate_libs_dir():
        print("[ERROR] libs/ directory not found")
        return 1

    if not check_poetry_available():
        print("[ERROR] Poetry is not available")
        return 1

    clean = '--clean' in sys.argv

    checked = 0
    problems = 0
    packages = 0
    local_mode = False

    for pyproject in find_pyproject_files():
        if any(dep['kind'] == 'local' for dep in read_sibling_dependencies(pyproject).values()):
            local_mode = True

        deps, issues = check_package(pyproject)
        if deps:
            packages += 1
        checked += deps
        problems += issues

    handle_stale_clones(clean, local_mode)

    print(f"\n[COMPLETE] {packages} packages checked, {checked} dependencies")
    if problems:
        print(f"  - Problems found: {problems}")
        return 1

    print("  - All dependencies resolve as declared")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as ex:
        print(f"\n[ERROR] {ex}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
