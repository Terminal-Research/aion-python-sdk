# aion.db

`aion.db` provides PostgreSQL models, migrations, and task repositories for the
Aion SDK. Its third-party dependencies — SQLAlchemy, Alembic, psycopg — come
with either agent server extra: `pip install "aionto-sdk[langgraph-server]"` or
`pip install "aionto-sdk[adk-server]"`.

The migrations in `aion.db.postgres.migrations` build every table in the `aion`
schema, including the ones on a2a-sdk's models; `docs/development/a2a-sdk-mapping.md`
maps each a2a-sdk revision to the SDK revision that applies it.
`aion.server.database` runs them together with the installed frameworks'
migrations, for `aion db migrate`, `aion db check` and a starting server.

See [`tests/integration/db/README.md`](../../../tests/integration/db/README.md) for running the integration tests.
