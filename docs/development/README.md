# Development Guide

Everything you need to start contributing to the Aion Python SDK.

## Contents

- **[Environment Setup](environment.md)** — Python version requirements and environment configuration
- **[Dependencies Management](dependencies.md)** — Installing the project, changing dependencies, and feature branch testing

## Testing

The whole suite lives in `tests/`, mirroring `src/aion/`, and runs as one
`pytest` invocation. Anything after `ARGS=` is passed to pytest untouched.

```bash
# Run the unit suite
make tests

# Run one subpackage's tests
make tests ARGS="tests/core tests/db"

# Stop on first failure
make tests ARGS="-x"
```

### Unit tests and integration tests

The suites are separated by the `integration` marker, and `make tests` runs
only the unit one. An integration test needs a real PostgreSQL to migrate and
truncate, or real child processes to signal, and it waits for real lease
timeouts. Run it before you commit rather than between two edits.

```bash
# Start a database, run the integration suite, stop the database
make tests-integration

# The same, with the unit suite as well
make tests-all
```

There is nothing to set up and nothing to clean up: both targets start a
disposable PostgreSQL container, run the suite, and stop the container
afterwards. A failing suite still fails the target - the exit status is carried
across the teardown.

```bash
# Keep the container up between runs while debugging one test
PG_TEST_KEEP=1 make tests-integration

# Drive the container by hand
make pg-test-up
make pg-test-down
```

To use a database of your own instead, set `POSTGRES_TEST_URL`. Docker is then
left alone entirely:

```bash
POSTGRES_TEST_URL=postgresql://user:pass@host:5432/db make tests-integration
```

The variable is deliberately not the ordinary `POSTGRES_URL`: these tests
migrate and truncate whatever they are pointed at, so an address has to be
given that meaning explicitly.

Mark a new test with `@pytest.mark.integration` whenever it needs something the
developer machine does not have by default.

## The layer contract

`src/aion/` is layered, and the layers are a contract in the root
`pyproject.toml` rather than a convention:

```bash
make lint-imports
```

Run it after moving code between subpackages. The layers, and the separate rule
that authoring subpackages never import server machinery, are described in
`AGENTS.md`.
