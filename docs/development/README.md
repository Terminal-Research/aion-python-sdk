# Development Guide

Everything you need to start contributing to the Aion Python SDK.

## Contents

- **[Environment Setup](environment.md)** — Python version requirements and environment configuration
- **[Dependencies Management](dependencies.md)** — Installing the project, changing dependencies, and feature branch testing
- **[Extension exposure](extension-exposure.md)** — Known, active, advertised, unavailable, and which extension owns a task
- **[Scenario tests](../../tests/scenarios/README.md)** — The suite that starts a real `aion serve` and drives it over A2A
- **[Continuous integration](ci.md)** — Full pull request checks and the GitHub ruleset that makes them required

This directory contains repository-level maintainer guides. Other Markdown in
the repository is scoped to where it sits — the root `README.md` and
`RELEASE.md`, subpackage README files, and one beside a test suite that needs
explaining. Everything user-facing — installation, `aion.yaml`, environment
variables, the CLI, the HTTP endpoints, the extension specifications — is
published at <https://docs.aion.to> from the
`aion-docs-mintlify` repository. It is not mirrored here, not even as
forwarding files: one copy of a subject is the point.

## Testing

The suite lives in three directories under `tests/`: `tests/unit`,
`tests/integration` and `tests/scenarios`. The first two
mirror `src/aion/`; the scenario suite mirrors nothing, it drives the product
from outside. The directory a test is in is what decides how it runs - the
root `conftest.py` puts the suite's marker on every item by directory, so a
module never says which suite it belongs to.

Anything after `ARGS=` is passed to pytest untouched; `TEST_PATHS=` narrows a
run to part of a suite, and a target accepts paths under its own suite's
directory only - `make tests-unit` will not run something under `tests/integration`
without the database the integration target sets up. The two are separate so
that a path never lands next to the suite's own directory and collects the
same tests twice.

```bash
# Run the unit suite
make tests-unit

# Run one subpackage's tests
make tests-unit TEST_PATHS="tests/unit/core tests/unit/db"

# Stop on first failure
make tests-unit ARGS="-x"

# One process instead of four xdist workers, for --pdb or -s
make tests-unit UNIT_WORKERS=0 ARGS="--pdb"
```

Run the affected module's tests while editing and the whole unit suite
before finishing a change.

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

# Every source-checkout test group, including all scenarios
make tests-full
```

There is nothing to set up and nothing to clean up: the integration target
starts a disposable PostgreSQL container, runs its suite, and stops the
container afterwards. `make tests-full` runs the unit, integration, and all
three scenario groups in sequence; each database-backed target manages its
own container. A failing suite stops the run and preserves its exit status.
Run it deliberately; CI runs the full source-checkout gate for every
pull request ([ci.md](ci.md)).

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
Every item in it carries the `scenario` marker. The individual unit and
integration targets do not run scenarios; `make tests-full` runs all three
scenario groups after those suites.

```bash
# Ordinary in-memory scenarios, against this working tree
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
