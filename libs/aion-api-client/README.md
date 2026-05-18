# Aion API Client

This library provides low-level Aion control-plane API access. It includes a
websocket GraphQL client generated with
[gql](https://gql.readthedocs.io/) and
[ariadne-codegen](https://ariadnegraphql.org/docs/ariadne-codegen), plus REST
configuration helpers used by higher-level Aion framework packages.

Settings are managed by
[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
and loaded from environment variables and `.env` files.

The client authenticates with the Aion API using a `client_id` and `client_secret`.
A JWT is obtained by POSTing these values to `/auth/tokens`. The returned token is
passed to the websocket endpoint `/ws/graphql` via the `token` query parameter.
These credentials must be supplied via the `AION_CLIENT_ID` and `AION_CLIENT_SECRET`
environment variables. The token is refreshed automatically when it expires.

## Usage

To use this client in another project, first set your credentials as environment
variables:

```bash
export AION_CLIENT_ID="your-client-id"
export AION_CLIENT_SECRET="your-secret"
```

Connection settings such as host and port are configured via environment variables.
You can set additional `AION_`-prefixed environment variables like `AION_API_HOST` or
`AION_API_KEEP_ALIVE` to customize the configuration. Alternatively, you can create
a `.env` file in your project root:

```
AION_CLIENT_ID=your-client-id
AION_CLIENT_SECRET=your-secret
AION_API_HOST=https://api.aion.to
AION_API_KEEP_ALIVE=60
```

## REST Endpoints

The REST helpers target these control-plane endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /auth/token` | Exchanges Aion client credentials for an API token. |
| `GET /v1/models` | Lists model-service catalog entries available through Aion. |
| `GET /v1/models/{model}` | Retrieves one model-service catalog entry. |
| `POST /v1/chat/completions` | Creates a model-service chat completion, including streaming responses when requested. |

## Development

Install the project using Poetry and run the tests with `pytest`:

```bash
cd libs/aion-api-client
poetry install
poetry run pytest
```

To regenerate the Python classes for the GraphQL API run:

```bash
poetry run ariadne-codegen client
```
