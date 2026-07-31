# Dependency Management Scripts

Scripts for managing local dependencies in the monorepo.

## Working on several packages at once

By default every library pulls its siblings from GitHub, so edits to
`libs/aion-core` are invisible to `libs/aion-server` until they are pushed. To
work across packages, switch the monorepo to local path dependencies:

```bash
make deps-use-local      # set-local apply + lock + install + verify
```

Sibling packages are then installed editable — imports resolve straight to the
working tree, and further edits take effect with no reinstall. The final step
verifies that this actually happened and prints one line per dependency, so a
silently broken environment is visible immediately rather than hours later.

If you ever doubt whether your edits are being picked up, ask directly:

```bash
make deps-verify
```

**Before committing**, restore the git references, otherwise the local paths
end up in the repository:

```bash
make deps-set-local-revert   # rewrites pyproject.toml only, environments untouched
git commit ...
make deps-set-local          # switch back and keep working
```

`poetry.lock` is git-ignored, so only `pyproject.toml` needs reverting. The lock
files will disagree with the reverted `pyproject.toml` until the next
`make deps-lock`; that is local-only noise, but `poetry install` will refuse to
run against a stale lock, so re-lock if you need to install in that window.

To leave local mode entirely and reinstall everything from git:

```bash
make deps-use-remote     # set-local revert + lock + install
```

## Quick Reference

All scripts can be run directly or via Make commands:

```bash
make deps-use-local       # Develop locally: local paths, then lock, install and verify
make deps-use-remote      # Return to git dependencies: revert, then lock and install
make deps-verify          # Check that packages resolve to the working tree
make deps-verify-clean    # Same, plus delete leftover .venv/src clones
make deps-install         # Install dependencies from lock files
make deps-lock            # Update lock files (incremental)
make deps-lock-regenerate # Regenerate lock files from scratch
make deps-sync            # Sync dependencies with lock files
make deps-set-branch BRANCH=features/my-branch  # Update git branch references
make deps-set-local       # Switch to local path dependencies (files only)
make deps-set-local-revert # Restore original git dependencies (files only)
```

## Configuration

There is none to maintain. Packages are discovered from the filesystem: every
directory under `libs/` holding a `pyproject.toml` is a Poetry package. Adding
or removing a library needs no change to these scripts.

`config.py` holds only the shared paths and that discovery helper.

## Scripts

### install.py
Install dependencies from lock files for all packages (runs
`poetry install --all-extras`).

Local path dependencies declared with `develop = true` are installed in
editable mode by Poetry itself — imports resolve straight to the working tree,
and no separate editable-install step is required.

Extras are always included. This monorepo wires some siblings through them:
`aion-sdk` reaches `aion.server` only via `aion-server-langgraph` and
`aion-server-adk`, both optional, so installing without extras leaves
`aion serve` reporting *"Server dependencies are not installed"*. The flag is a
no-op for packages that declare no extras.

```bash
./scripts/deps/install.py
```

### verify.py
Check that every environment resolves its sibling packages the way
`pyproject.toml` declares, and report leftover `.venv/src` clones.

Reads each installed distribution's PEP 610 `direct_url.json` rather than
guessing module names, so it reports precisely whether a package is editable, a
non-editable copy, installed from git, or missing. Exits non-zero when anything
disagrees, which makes it usable as a pre-flight check in CI or a git hook.

```bash
./scripts/deps/verify.py           # report only
./scripts/deps/verify.py --clean   # also delete leftover .venv/src clones
```

### lock.py
Update lock files for all packages (runs `poetry lock`).
```bash
./scripts/deps/lock.py
```

### set-branch.py
Update git branch references in all `pyproject.toml` files. Essential for testing changes in feature branches before merging to main.

**Problem:** When installing packages from a feature branch via git, transitive dependencies still reference `main` branch, preventing proper testing of changes.

**Solution:** This script updates all internal git dependencies to reference the same branch.

```bash
# Update all branches for feature branch testing
./scripts/deps/set-branch.py features/my-feature

# Restore all branches to main before merging
./scripts/deps/set-branch.py main
```

**Workflow for testing feature branches:**
1. Work on your feature branch (e.g., `features/my-feature`)
2. Update branch references: `./scripts/deps/set-branch.py features/my-feature`
3. Commit changes: `git commit -am "chore: update branches for testing"`
4. Push and test installation from git
5. Before PR/merge: `./scripts/deps/set-branch.py main`
6. Commit: `git commit -am "chore: restore branches to main"`

### set-local.py
Toggle between remote git dependencies and local path dependencies. Essential for testing local changes without committing to git.

**Problem:** When developing features that span multiple packages, you need to test changes across packages without creating commits and pushing to remote branches.

**Solution:** This script converts git dependencies to local relative paths, allowing you to test uncommitted changes immediately. Original git references are preserved in comments for easy restoration.

```bash
# Switch to local path dependencies
./scripts/deps/set-local.py apply

# Restore original git dependencies
./scripts/deps/set-local.py revert
```

**What it does:**
- Converts `git = "..."` dependencies to `path = "../package-name"`
- Uses `tomllib` for reliable parsing (handles all formatting variations)
- Preserves `extras` and `optional` attributes
- Comments original lines for safe rollback
- Works only with internal `libs/` packages

**Example transformation:**
```toml
# Before
aion-shared = { git = "https://github.com/...", branch = "main", subdirectory = "libs/aion-shared" }
aion-plugin-langgraph = { git = "...", branch = "main", subdirectory = "libs/aion-plugin-langgraph", optional = true }

# After apply
# [ORIGINAL-DEP] aion-shared = { git = "https://github.com/...", branch = "main", subdirectory = "libs/aion-shared" }
# [LOCAL-DEP]
aion-shared = { path = "../aion-shared" }
# [ORIGINAL-DEP] aion-plugin-langgraph = { git = "...", branch = "main", subdirectory = "libs/aion-plugin-langgraph", optional = true }
# [LOCAL-DEP]
aion-plugin-langgraph = { path = "../aion-plugin-langgraph", optional = true }
```

This script only rewrites `pyproject.toml`; it does not touch environments. See
[Working on several packages at once](#working-on-several-packages-at-once) for
the full sequence, which `make deps-use-local` and `make deps-use-remote` wrap.

**Caveat:** Poetry decides whether to reinstall by comparing name, version and
source. It does notice a source change — switching a dependency between git and
a local path reinstalls it — but it does not track whether the installed
distribution is editable. If an environment ends up holding a non-editable copy
of a sibling at the same version and source, `poetry install`, `poetry sync` and
even `poetry lock --regenerate` all report `No dependencies to install or update`
and leave the stale copy in place. To check what an environment actually
resolves:

```bash
cd libs/aion-server
poetry run python -c "import aion.core, os; print(os.path.dirname(aion.core.__file__))"
```

A path under `libs/aion-core/src` is correct; a path under `.venv/.../site-packages`
means a stale copy. Two ways to force it back:

```bash
poetry run pip install -e ../aion-core --no-deps   # direct
poetry run pip uninstall -y aion-core && poetry install   # let Poetry redo it
```

**Do not read `.venv/src/aion-python-sdk/`.** While the dependencies were git
references, Poetry cloned the whole monorepo there. Nothing uses those clones
once the dependencies are local paths — no `.pth` or `direct_url.json` points at
them and they never appear in `aion.__path__` — but they stay on disk, frozen at
whatever commit they were cloned from. Edits to `libs/aion-core` will never show
up there, which reads exactly like a broken editable install. Always check by
import, never by browsing paths inside `.venv`. They are safe to delete:
`rm -rf libs/*/.venv/src`.

**Important:** Do not commit files after running `apply`. Always run `revert` before committing to restore git dependencies.

### sync.py
Sync dependencies with lock files, removing any unlocked packages (runs `poetry sync`).
```bash
./scripts/deps/sync.py
```