# Local Dependencies Management

Guide for working with local packages in the `libs/` directory.

## Commands

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

1. **Start development**: `make install-local`
2. **Edit files** in `libs/` directories  
3. **Test changes** - they're live immediately
4. **When done**: `make restore-local` (optional - to test with published versions)