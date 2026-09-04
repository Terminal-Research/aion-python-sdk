# aion.db

`aion.db` provides PostgreSQL models, migrations, and task repositories for the
Aion SDK. Its third-party dependencies — SQLAlchemy, Alembic, psycopg — come
with either agent server extra: `pip install "aionto-sdk[langgraph-server]"` or
`pip install "aionto-sdk[adk-server]"`.

See [`tests/db/README.md`](../../../tests/db/README.md) for running the integration tests.
