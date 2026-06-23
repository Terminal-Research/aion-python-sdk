# Development Guide

Everything you need to start contributing to the Aion Python SDK.

## Contents

- **[Environment Setup](environment.md)** — Python version requirements and environment configuration
- **[Dependencies Management](dependencies.md)** — Working with local packages, lock files, and feature branch testing

## Testing

The test runner automatically discovers all `libs/aion-*` packages and runs `pytest` in each one that has a `tests/` directory. Libs without tests are silently skipped.

```bash
# Run all libs
make tests

# Run specific libs
python scripts/tests.py aion-core aion-db

# Stop on first failure
python scripts/tests.py --fail-fast
```

Each lib runs `poetry run pytest` in its own directory, so dependencies are isolated per package.
