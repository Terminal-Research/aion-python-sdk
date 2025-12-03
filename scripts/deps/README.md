# Dependency Management Scripts

Scripts for managing local dependencies in the monorepo.

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

### sync.py
Sync dependencies with lock files, removing any unlocked packages (runs `poetry sync`).
```bash
./scripts/deps/sync.py
```