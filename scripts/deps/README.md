# Dependency Management Scripts

Scripts for managing local dependencies in the monorepo.

## Configuration

Edit `config.py` to add/remove packages or change dependency mappings.

## Scripts

### install.py
Install dependencies from lock files for all packages (runs `poetry install --sync`).
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

## Usage via Makefile

**Development workflow:**
```bash
make deps-install-dev   # Link local packages for development
# Make changes...
make deps-lock          # Update lock files
make deps-install       # Restore PyPI versions
```

**Update dependencies:**
```bash
make deps-lock          # Update lock files
make deps-install       # Install updated dependencies
```
