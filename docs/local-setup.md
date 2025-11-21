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

### Install dependencies
```bash
make deps-install
```
Installs dependencies from existing lock files for all packages (runs `poetry install --sync`).
**Note:** This removes editable installations and syncs environment with lock files.

### Install local dependencies for development
```bash
make deps-install-dev
```
Installs packages from `libs/` in editable mode. Changes are immediately reflected.

### Update lock files
```bash
make deps-lock
```
Updates `poetry.lock` files for all packages (runs `poetry lock`).

## Workflows

### Development with local packages
1. **Start development**: `make deps-install-dev`
2. **Edit files** in `libs/` directories
3. **Test changes** - they're live immediately
4. **When done**: `make deps-install` (to restore PyPI versions)

### Update dependencies
1. **Update lock files**: `make deps-lock`
2. **Install updated dependencies**: `make deps-install`

Or combine both:
```bash
make deps-lock && make deps-install
```

## Configuration

To modify which packages are processed or change dependency mappings, edit `scripts/deps/config.py`.

For more details, see `scripts/deps/README.md`.