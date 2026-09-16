# Development Guide

Everything you need to start contributing to the Aion Python SDK.

## Contents

- **[Environment Setup](environment.md)** — Python version requirements and environment configuration
- **[Dependencies Management](dependencies.md)** — Installing the project, changing dependencies, and feature branch testing
- **[Scenario tests](../../tests/scenarios/README.md)** — The suite that starts a real `aion serve` and drives it over A2A

## Testing

The suite lives in `tests/`, in three directories that are the three ways it
is run: `tests/unit`, `tests/integration` and `tests/scenarios`. The first two
mirror `src/aion/`; the scenario suite mirrors nothing, it drives the product
from outside. The directory a test is in is what decides how it runs - the
root `conftest.py` puts the suite's marker on every item by directory, so a
module never says which suite it belongs to.

Anything after `ARGS=` is passed to pytest untouched; `TEST_PATHS=` narrows a
run to part of a suite. They are separate so that a path never lands next to
the suite's own directory and collects the same tests twice.

```bash
# Run the unit suite
make tests

# Run one subpackage's tests
make tests TEST_PATHS="tests/unit/core tests/unit/db"

# Stop on first failure
make tests ARGS="-x"
```

### Unit tests and integration tests

A unit test runs on any developer machine with nothing set up. It may use
`tmp_path`, a loopback socket or an in-process ASGI client, and it may start a
short child Python to see what a thinner installation imports. An integration
test is about a real infrastructure boundary or an OS-level lifecycle, where
the real behaviour is the subject: a PostgreSQL to migrate and truncate, a
real process tree and its descendants to signal, real lease timeouts to wait
for. Run it before you commit rather than between two edits.

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

Put a new test under `tests/integration` whenever it needs something the
developer machine does not have by default; the marker follows from the
directory.

### Scenario tests

A third suite lives in `tests/scenarios`. It starts a real `aion serve` for
each framework and deployment variant, talks to the agents through the proxy
with an ordinary A2A client, and asserts on what comes back over the wire.
Every item in it carries the `scenario` marker, and none of the targets above
runs it: a suite that starts processes is not what you run between two
edits.

```bash
# The whole suite, against this working tree
make tests-scenarios

# One suite, one framework
make tests-scenarios TAGS=smoke FRAMEWORK=adk

# Against the wheel in dist/, installed into a clean venv
make dist-build && make tests-scenarios-dist
```

Run it when a change touches what goes over the wire;
`make tests-scenarios-dist` is a step of the release gate.
[tests/scenarios/README.md](../../tests/scenarios/README.md) is the whole of it
- how to write one, where the line with the unit tests runs, and how to add a
command or a framework - and
[tests/scenarios/SCENARIOS.md](../../tests/scenarios/SCENARIOS.md) beside it is
the generated matrix of every scenario, command and framework.

## The layer contract

`src/aion/` is layered, and the layers are a contract in the root
`pyproject.toml` rather than a convention:

```bash
make lint-imports
```

Run it after moving code between subpackages. The layers, and the separate rule
that authoring subpackages never import server machinery, are described in
`AGENTS.md`.
