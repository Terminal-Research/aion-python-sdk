# Tests

The test suite is split by execution boundary. A test belongs to a suite
because of the directory it lives in; do not add `unit`, `integration`, or
`scenario` markers by hand.

| Suite | Purpose | Run |
|---|---|---|
| [`unit/`](unit/) | Runs without external infrastructure | `make tests` |
| [`integration/`](integration/) | Exercises real PostgreSQL or process lifecycle behavior | `make tests-integration` |
| [`scenarios/`](scenarios/) | Starts a real `aion serve` and drives it over A2A | `make tests-scenarios` |

`unit/` and `integration/` mirror `src/aion/`. The scenario suite has its own
agents, deployment configurations, and harness because it tests the product
through its public protocol rather than by importing the component under test.

## Selecting tests

Run tests through the Make targets so each suite receives the environment and
teardown it needs. Use `TEST_PATHS` to select files or directories within the
target's suite, and `ARGS` for pytest options:

```bash
make tests TEST_PATHS="tests/unit/server"
make tests ARGS="-k websocket -x"
make tests-integration TEST_PATHS="tests/integration/db"
```

Each target rejects `TEST_PATHS` outside its own suite. `make tests-all` runs
the unit and integration suites together. Scenario-specific selection uses
`TAGS` and `FRAMEWORK`; see the scenario guide for the complete interface.

## Shared test code

Keep helpers with the suite that owns them. Builders shared by unit test
modules live in [`unit/support/`](unit/support/). Move a helper to a top-level
`support/` package only when it has consumers in more than one suite.

Shared pytest fixtures live in the nearest `conftest.py` whose scope covers
all of their consumers. Test modules should not import helpers from other test
modules.

## More information

- [Development testing guide](../docs/development/README.md)
- [PostgreSQL integration tests](integration/db/README.md)
- [Scenario suite guide](scenarios/README.md)
- [Generated scenario matrix](scenarios/SCENARIOS.md) — a readable inventory
  of collected scenarios and coverage, not a test-run report

