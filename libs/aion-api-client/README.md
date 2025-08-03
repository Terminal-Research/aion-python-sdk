# Aion API Client

This library provides a websocket GraphQL client for the Aion API. It uses
[gql](https://gql.readthedocs.io/) and [ariadne-codegen](https://ariadnegraphql.org/docs/ariadne-codegen)
to generate typed operations from the provided GraphQL schema. Settings are
managed by [Dynaconf](https://www.dynaconf.com/) and loaded from
`aion_api_client.yaml`.

The client authenticates with the Aion API using a `client_id` and `secret_key`.
A JWT is obtained by POSTing these values to `/auth/token`. The returned token is
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

Connection settings such as host and port are configured in
`aion_api_client.yaml`. You can override any of these values with additional
`AION_`-prefixed environment variables like `AION_AION_API_HOST` or
`AION_AION_API_PORT` if needed.

Next, create and initialize the GraphQL client before making requests:

```python
import asyncio
from aion.api.gql.client import AionGqlClient, MessageInput

async def main():
    client = AionGqlClient()  # reads credentials from environment
    await client.initialize()
    async for chunk in client.chat_completion_stream(
        model="example-model",
        messages=[MessageInput(role="user", content="Hello")],
        stream=True,
    ):
        print(chunk)

asyncio.run(main())
```

You can also supply credentials directly when constructing the client:

```python
client = AionGqlClient(client_id="your-client-id", client_secret="your-secret")
```

## Development

Install the project using Poetry and run the tests with `pytest`:

```bash
cd libs/aion-api-client
poetry install
poetry run pytest
```

To regenerate the Python classes for the GraphQL API run:

```bash
poetry run ariadne-codegen
```