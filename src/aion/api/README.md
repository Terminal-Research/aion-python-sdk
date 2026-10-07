# aion.api

Low-level Aion control-plane API access. It includes a websocket GraphQL client
generated with [gql](https://gql.readthedocs.io/) and
[ariadne-codegen](https://ariadnegraphql.org/docs/ariadne-codegen), plus REST
configuration helpers used by the subpackages above it.

Always installed: it is part of the base `pip install aionto-sdk`, and no extra
adds anything to it.

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
| `POST /auth/tokens` | Exchanges Aion client credentials for an API token. |
| `GET /v1/models` | Lists model-service catalog entries available through Aion. |
| `GET /v1/models/{model}` | Retrieves one model-service catalog entry. |
| `POST /v1/chat/completions` | Creates a model-service chat completion, including streaming responses when requested. |
| `POST /files/agent-artifacts` | Creates protected agent output. |
| `PUT /files/{fileId}` | Replaces a File under an exact version/revision fence. |

### Files

`AionFileClient` obtains a current bearer token and, inside an Aion runtime
request, forwards its opaque usage-attribution carrier. The server verifies its
executor and checks current authority. Explicit callback selector overrides are
rejected rather than silently selecting a different identity.

```python
from aion.api import AionFileClient

async with AionFileClient() as files:
    uploaded = await files.create_agent_artifact(
        b"message attachment",
        organization_id="organization-id",
        file_name="message.txt",
        media_type="text/plain",
    )
    shared = await files.create_read_grant(
        uploaded["id"], uploaded["versionId"], ttl_minutes=60,
    )
```

Use `create_profile_image` for avatar/background uploads, `get_metadata`
for safe current facts, and `renew_retention` with a required absolute
timezone-aware deadline or explicit `None`. A grant does not renew storage.
See [File Service](https://docs.aion.to/docs/resources/file-service) for the
owner, permission, and retention contract.

## Control-plane addressing

Use `CapabilitySubject`, `CapabilityReference`, `PrincipalSelector`, and
`AionControlPlanePaths` when constructing A2A or MCP control-plane requests.
`CapabilityReference` is the SDK-level address shape for capability URLs:
optional subject, capability kind, and capability-key selector. A missing key
selects the primary capability for that subject and kind; it is not encoded as
the literal string `"primary"`. Subjectless system MCP references should be
keyed; `CapabilityReference.global_mcp()` selects the built-in metatools key
and resolves to `/mcp/capabilities/mcp.aion.metatools`.

```python
from aion.api import (
    AionControlPlanePaths,
    CapabilityReference,
    CapabilitySubject,
    CapabilitySubjectSource,
    PrincipalSelector,
    RuntimeCapabilityReference,
)

subject = CapabilitySubject.environment("agent-environment-id")
principal = PrincipalSelector.agent_environment("agent-environment-id")
paths = AionControlPlanePaths()

primary_mcp_url = paths.capability_url(CapabilityReference.primary_mcp(subject))
twitter_mcp_url = paths.capability_url(
    CapabilityReference.mcp(subject, key="mcp.twitter.distribution")
)
a2a_url = paths.capability_url(CapabilityReference.primary_a2a(subject))
graphql_principal = principal.to_gql_value()

runtime_mcp_reference = RuntimeCapabilityReference.primary_mcp(
    CapabilitySubjectSource.INCOMING_DISTRIBUTION
)
```

Principal selectors serialize as abstract Aion resource URIs, such as
`aion://agent/environment/{id}`.

The GraphQL client accepts these SDK model objects at its boundary and converts
them to their canonical resource URI strings before sending requests.

## Development

Install the project from the repository root and run this subpackage's tests:

```bash
poetry install -E langgraph-server -E adk-server --with dev
make tests-unit TEST_PATHS="tests/unit/api"
```

To regenerate the Python classes for the GraphQL API run:

```bash
poetry run ariadne-codegen client
```

SDK callbacks keep the Version bearer and forward `Aion-Usage-Attribution`.
Do not send `Aion-Principal-Selector` or exchange the bearer for a daemon token.
Capability subjects still address the destination; they do not select authority.

For a direct accepted SDK request, the runtime instead reports its canonical
caller through `Aion-Caller-Id`. Anonymous sessions retain their session ID;
an unidentified accepted caller is `ExternalAnonymous`. This value is attribution
only. Aion resolves the deployment's current daemon and payer on each callback;
the SDK does not cache an assignment or use the caller's bearer for these calls.
Without deployment credentials, a guest session cannot access metered APIs.

`AionDaemonIdentityRequired` (also exported from `aion.core.exceptions`) carries
the stable `daemon_identity_required` code and `retryable = False`. Assign a
daemon in the deployment/agent environment Identity tab before retrying; the
message names the resource Aion reports as missing it, such as the deployment
and its id. Model
and MCP adapters preserve this configuration error; successful response streams
are not buffered for error inspection. Model helpers default to no transport
retry, so missing configuration is not disguised by repeated connection attempts.

File create/replace and GraphQL A2A subscriptions use the same callback scope.
GraphQL carries the exclusive input in `serviceParameters.additional`; it does
not need an independent `principal` argument. File replacement still requires
its normal update permission and revision fence. Artifact uploads may omit
`organization_id`: Aion derives it from verified callback attribution or the
deployment's current payer. An explicit organization must match that payer.
Neither distribution recipients nor reported callers select the File owner.

The Aion storage backend obtains an exact-version grant after upload and emits
that URL for recipient delivery. Grant retries do not repeat a confirmed upload.
Receipts retain File/version IDs and separate access/retention deadlines; stored
history links are not renewed automatically. No finite retention is added unless
the uploader supplies an absolute deadline. Grant failure is a delivery failure,
even if the upload committed; it never falls back to a protected content URL.
