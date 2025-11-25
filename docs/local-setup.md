# Local Dependencies Management

Guide for working with local packages in the `libs/` directory.

## Quick Reference

To see all available commands:
```bash
make
# or
make help
```

## Commands

### Install local dependencies for development
```bash
make deps-install-dev
```
Installs packages from `libs/` in editable mode using `poetry run pip install -e`. Changes to local packages are immediately reflected without reinstallation.

**Use case**: Active development across multiple packages in the monorepo.

### Install dependencies from lock files
```bash
make deps-install
```
Installs dependencies from existing lock files for all packages (runs `poetry install`).

**Note:** This installs packages from PyPI according to lock files, replacing any editable installations.

### Sync dependencies with lock files
```bash
make deps-sync
```
Synchronizes environments with lock files by running `poetry sync`. This removes any packages not specified in `poetry.lock`.

**Use case**: Clean up environment after removing dependencies or switching branches.

### Update lock files (incremental)
```bash
make deps-lock
```
Updates `poetry.lock` files for all packages by running `poetry lock`.

**Behavior**: Performs incremental update, preserving existing locked versions that still satisfy constraints in `pyproject.toml`.

**Use case**: After adding or modifying dependencies in `pyproject.toml`.

### Regenerate lock files from scratch
```bash
make deps-lock-regenerate
```
Completely regenerates `poetry.lock` files from scratch using `poetry lock --regenerate`.

**Behavior**: Ignores existing lock files and resolves all dependencies anew.

**Use case**: Resolving dependency conflicts or forcing update to latest compatible versions.

## Workflows

### Development with local packages
1. **Start development**:
   ```bash
   make deps-install-dev
   ```
2. **Edit files** in `libs/` directories - changes are live immediately
3. **Test changes** without reinstalling
4. **When done**, restore PyPI versions:
   ```bash
   make deps-install
   ```

### Add or update dependencies
1. **Edit** `pyproject.toml` in the package directory
2. **Update lock files** (incremental):
   ```bash
   make deps-lock
   ```
3. **Install updated dependencies**:
   ```bash
   make deps-install
   ```

Or combine both:
```bash
make deps-lock && make deps-install
```

### Clean up environment
After removing dependencies or switching branches:
```bash
make deps-sync
```
This removes packages not in lock files.

### Resolve dependency conflicts
If you encounter dependency conflicts:
```bash
make deps-lock-regenerate  # Rebuild locks from scratch
make deps-sync              # Clean install
```

## Configuration

To modify which packages are processed or change dependency mappings, edit `scripts/deps/config.py`.