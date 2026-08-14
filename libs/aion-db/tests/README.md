# aion-db tests

## PostgreSQL integration tests

The repository tests use a real PostgreSQL instance and are opt-in through
`POSTGRES_TEST_URL`. Start a disposable local database with:

```bash
docker run --rm --name aion-db-test -e POSTGRES_PASSWORD=postgres -p 54329:5432 -d postgres:16
```

Then run the database tests from the SDK repository:

```bash
POSTGRES_TEST_URL=postgresql://postgres:postgres@localhost:54329/postgres \
  make tests ARGS="aion-db"
```

Stop the container when finished:

```bash
docker stop aion-db-test
```

Without `POSTGRES_TEST_URL`, the PostgreSQL integration tests are skipped.
