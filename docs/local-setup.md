# Local Dependencies Management

Guide for working with local packages in the `libs/` directory.

## Commands

### Install all dependencies in libs directories
```bash
make install-deps
```
Installs all dependencies for packages in `libs/` directories.

### Install local dependencies for development
```bash
make install-local
```
Installs packages from `libs/` in editable mode. Changes are immediately reflected.

### Restore original dependencies  
```bash
make restore-local
```
Restores published versions from `pyproject.toml`.

## Workflow

1. **Install dependencies**: `make install-deps`
2. **Start development**: `make install-local`
3. **Edit files** in `libs/` directories  
4. **Test changes** - they're live immediately
5. **When done**: `make restore-local` (optional - to test with published versions)