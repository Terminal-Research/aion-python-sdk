# aion-db tests

## PostgreSQL integration tests

The repository tests use a real PostgreSQL instance and carry the `integration`
marker, so `make tests` does not run them. Run them before you commit:

```bash
make tests-integration ARGS="tests/db"
```

The target starts a disposable PostgreSQL container, runs the suite, and stops
the container afterwards. `PG_TEST_KEEP=1` leaves it running between runs.

To use a database of your own, set `POSTGRES_TEST_URL` and nothing will touch
Docker:

```bash
POSTGRES_TEST_URL=postgresql://user:pass@host:5432/db make tests-integration ARGS="tests/db"
```

These tests migrate and truncate the database they are pointed at. Never point
`POSTGRES_TEST_URL` at one whose contents matter.
