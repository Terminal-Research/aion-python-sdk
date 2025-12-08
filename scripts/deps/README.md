# Dependency Management Scripts

Scripts for managing local dependencies in the monorepo.

## Quick Reference

All scripts can be run directly or via Make commands:

```bash
make deps-install         # Install dependencies from lock files
make deps-install-dev     # Install local dependencies in editable mode
make deps-lock            # Update lock files (incremental)
make deps-lock-regenerate # Regenerate lock files from scratch
make deps-sync            # Sync dependencies with lock files
make deps-set-branch BRANCH=features/my-branch  # Update git branch references
make deps-set-local       # Switch to local path dependencies
make deps-set-local-revert # Restore original git dependencies
```

## Configuration

Edit `config.py` to add/remove packages or change dependency mappings.

## Scripts

### install.py
Install dependencies from lock files for all packages (runs `poetry install`).
Removes editable installations and syncs environment with lock files.
```bash
./scripts/deps/install.py
```


### install-dev.py
Install local dependencies in editable mode for development.
```bash
./scripts/deps/install-dev.py
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

**Workflow for local testing:**
1. Make changes across multiple packages (e.g., `aion-shared`, `aion-server`)
2. Apply local paths: `./scripts/deps/set-local.py apply`
3. Install dependencies: `./scripts/deps/install-dev.py`
4. Test your changes locally
5. When satisfied, revert: `./scripts/deps/set-local.py revert`
6. Commit and push your changes

**Important:** Do not commit files after running `apply`. Always run `revert` before committing to restore git dependencies.

### sync.py
Sync dependencies with lock files, removing any unlocked packages (runs `poetry sync`).
```bash
./scripts/deps/sync.py
```