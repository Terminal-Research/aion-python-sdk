# Tests

Tests are organized by execution boundary. The three directories are
`tests/unit`, `tests/integration`, and `tests/scenarios`. The scenario suite
has three run groups, so the Make interface exposes five explicit test
targets. The directory supplies each test's `unit`, `integration`, or
`scenario` marker; do not add those markers by hand.

| Group | What it verifies | Command |
| --- | --- | --- |
| Unit | SDK behavior without external infrastructure | `make tests-unit` |
| Integration | Real PostgreSQL or OS process lifecycle inside the SDK | `make tests-integration` |
| Ordinary scenarios | A real `aion serve` reached through the proxy over A2A, using in-memory deployment variants | `make tests-scenarios` |
| Persistence scenarios | Task state and recovery across a server restart with PostgreSQL | `make tests-scenarios-persistence` |
| Distributed scenarios | Ownership, cancellation, and recovery across two servers sharing PostgreSQL | `make tests-scenarios-distributed` |

`tests/unit` and `tests/integration` mirror `src/aion`. Scenarios drive the
product through its public protocol rather than importing the component under
test. The ordinary scenario target excludes the persistence and distributed
groups; each database-backed group has its own target.

## Running tests

Run tests through Make so each group receives its own setup and teardown.
While editing, run the tests of the affected module. Before finishing a
change, run the whole unit suite with `make tests-unit`, plus the integration
or scenario group that covers a changed boundary.

`make tests-full` runs all five source-checkout groups in sequence, without
narrowing selectors. It does not build or test the distribution wheel. The
database-backed groups share one local container and port, so they cannot run
side by side. Run it when a complete local validation is needed; CI
runs the full source-checkout gate on every pull request ([CI guide](../docs/development/ci.md)).

`make tests-unit` runs on pytest-xdist workers. `UNIT_WORKERS` sets the
count (default 4; any `pytest -n` value). `UNIT_WORKERS=0` runs the suite in
one process, which `--pdb` and `-s` need.

Use `TEST_PATHS` for a unit or integration subset and `ARGS` for pytest
options:

```bash
make tests-unit TEST_PATHS="tests/unit/server"
make tests-unit ARGS="-k websocket -x"
make tests-unit UNIT_WORKERS=0 ARGS="--pdb"
make tests-integration TEST_PATHS="tests/integration/db"
```

Each target rejects `TEST_PATHS` outside its own suite. Scenario-specific
selection uses `TAGS` and `FRAMEWORK`; see the scenario guide.

The integration and database-backed scenario targets start a disposable
PostgreSQL container unless `POSTGRES_TEST_URL` is provided. These tests
migrate and truncate the specified database, so supply only a disposable test
database. `PG_TEST_KEEP=1` retains the local container while debugging.

## Shared test code

Keep helpers with the suite that owns them. Builders shared by unit test
modules live in [`unit/support/`](unit/support/). Move a helper to a top-level
`support/` package only when more than one suite consumes it.

Shared pytest fixtures live in the nearest `conftest.py` whose scope covers
all consumers. Test modules should not import helpers from other test modules.

## More information

- [Development testing guide](../docs/development/README.md)
- [Continuous integration](../docs/development/ci.md) — full pull request
  checks and the GitHub ruleset that makes them required
- [PostgreSQL integration tests](integration/db/README.md)
- [Scenario suite guide](scenarios/README.md)
- [Generated scenario matrix](scenarios/SCENARIOS.md) — collected scenarios
  and coverage, not a test-run report
