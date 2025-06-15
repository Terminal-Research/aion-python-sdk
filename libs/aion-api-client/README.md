# Aion API Client

This library provides a websocket GraphQL client for the Aion API. It uses
[gql](https://gql.readthedocs.io/) and [ariadne-codegen](https://ariadnegraphql.org/docs/ariadne-codegen)
to generate typed operations from the provided GraphQL schema. Settings are
managed by [Dynaconf](https://www.dynaconf.com/) and loaded from
`aion_api_client.yaml`.

The client authenticates with the Aion API using a `client_id` and `secret_key`.
A JWT is obtained by POSTing these values to `/auth/token`. The returned token is
passed to the websocket endpoint `/ws/graphql` via the `token` query parameter.
These credentials must be supplied via the `AION_CLIENT_ID` and `AION_SECRET`
environment variables. The token is refreshed automatically when it expires.

## Development

Install the project using Poetry and run the tests with `pytest`:

```bash
cd libs/aion-api-client
poetry install
poetry run pytest
```

To regenerate the Python classes for the GraphQL API run:

```bash
poetry run ariadne-codegen gql/schema.graphql --output src/aion/gql/generated
```
