# aion.server

Implementation of an A2A protocol server that wraps an agent written with a
supported framework.

Installed with one of the agent server extras — `pip install
"aionto-sdk[langgraph-server]"` or `pip install "aionto-sdk[adk-server]"`,
depending on the framework the agents are written with. The subpackage itself
ships in every installation; the extra is what brings the `a2a-sdk` server
stack, Starlette and FastAPI it is built on.

This subpackage exposes an `AppFactory` that assembles the agent application on
top of the Google `a2a-sdk` and Starlette.

Graphs are registered based on an `aion.yaml` file located in your project
root. For detailed configuration options and examples, see the [Aion YAML
Configuration Guide](https://docs.aion.to/sdk/python/configuration/aion-yaml).

Custom HTTP endpoints are added through `AppRegistry`: the agent module
registers its own FastAPI routers before the server starts, and the factory
mounts them on the application it builds. With platform credentials those
routes need a bearer token like every other route of the server (see below).
`aion.yaml` declares agents and the
MCP proxy, and rejects any other key - see the [AppRegistry
guide](https://docs.aion.to/sdk/python/extensibility/app-registry).

## Who a task belongs to

Every task, and the framework state behind it, belongs to one owner. The
owner is what `AionAgent.owner_resolver` names for a request's
`ServerCallContext` - by default a2a-sdk's `resolve_user_scope`, the user's
name. `AppFactory` builds the task store with that same resolver and refuses
to start if a store was built with another, so the tasks table's
`owner_scope`, LangGraph's checkpoint `thread_id` and ADK's session `user_id`
always name the same owner, and so do the task's push notification configs.
A custom resolver is passed to the agent:
`AionAgent.from_adapter(..., owner_resolver=...)`; it receives the request's
`ServerCallContext`, whose user is the caller described below.

### Where the user comes from

Every request to an agent's server carries a bearer token,
`Authorization: Bearer <JWT>` - the JSON-RPC endpoint, the OpenAPI schema and
any route an application adds through `AppRegistry` alike. Only the public
paths are open without one: the agent card, health and the configuration
schema, which a client or a probe reads before it has a token. The agent card
declares the bearer scheme in its `securitySchemes`; the token is issued by the
Aion control plane. A request without a token, or with one that does not
verify, is answered `401` before its body is read, and the agent never runs.

`AppFactory` builds a `TokenVerifier` (`aion.server.auth.build_token_verifier`)
and hands it to `AionAuthMiddleware`, which it installs outside the server's
other middlewares. The middleware installs the caller the way Starlette's
authentication does: an `AuthenticatedCaller` as `request.scope["user"]` and
`authenticated` credentials as `request.scope["auth"]`. a2a-sdk's
`DefaultServerCallContextBuilder` makes them `ServerCallContext.user` and
`state["auth"]`, and `resolve_user_scope` makes the token's `sub` the owner.
The token's other claims, `subject_type` among them, stay on the caller
(`AuthenticatedCaller.claims`) for the agent's own logic. The distribution a
request came through is its channel, not its owner. Whatever user and
credentials a middleware in front of `AionAuthMiddleware` set are replaced.

Both kinds of token are signed JWTs (JWS). The verifier reads the token's
unverified `aud` only to choose the key, then verifies the whole token with it,
`aud` included. An `aud` that names neither kind is refused.

| | Call token | Anonymous session token |
| --- | --- | --- |
| `aud` | this server's `AION_CLIENT_ID` | `aion-anonymous-session` |
| Key | the platform's key for this deployment | the control plane's published keys, by the token's `kid` |
| Algorithm | ES256 | the key's own: ES256, ES384, ES512, RS256, PS256 or EdDSA |
| Required claims | `aud`, `exp`, `sub` | `iss` = `aion`, `aud`, `subject_type` = `AnonymousSession`, `exp`, `sub` |
| Server deployed by the Aion platform | accepted | refused |
| Any other server | accepted with `AION_CLIENT_ID` and `AION_CLIENT_SECRET` set, refused without | accepted |

A token also has to be current (`exp`, and `nbf` when it has one). A kind a
server does not accept in its situation is refused without any cryptography,
and the reason in the `401` never quotes the token. A call token may carry
`subject_type` = `AnonymousSession`; nothing restricts it.

The call token's key comes from the control plane, one key per client id. The
request for it is a stub for now (`PlatformKeySource._fetch_key`), so call
tokens are refused.

Anonymous session keys are the JWKS at `{AION_API_HOST}/runtime/a2a/verification-keys`,
a public endpoint that needs no credentials (`JwksKeySource`):

- The keys load at startup. A failed load does not stop the server; anonymous
  session tokens are refused until a later fetch succeeds.
- A key set older than 10 minutes is refreshed in the background while the
  current request is verified with the keys held. One refresh runs at a time.
- A token whose `kid` is not in the set triggers an immediate fetch, at most
  one every 30 seconds. If the key is still missing the token is refused.
- A failed refresh keeps the old set and logs a warning. Keys are never
  dropped by age.
- After a failure the refresh is retried at most once every 30 seconds.

The startup log says whether the server is hosted on the Aion platform.

Other headers name nobody. `ServerCallContext.tenant` is not a source either:
the JSON-RPC dispatcher copies it from the request's own `tenant` field, which
the client chooses, and the default resolver ignores it. There is no `/docs` or
`/redoc`.

Aion's `GetContexts` and `GetContext`, and finding an interrupted task through
its `contextId`, go through the same owner filter as everything else: a
caller sees its own contexts and continues its own interrupted task. Only a call without a `ServerCallContext` - from Python code
that holds the handler - reads no history at all.

### What is isolated

Every A2A operation is limited to the caller's owner, on the in-memory and
the PostgreSQL store alike: `SendMessage` and `SendStreamingMessage`
(including a `taskId` to continue), `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask`, the push notification config methods, and Aion's
`GetContexts`/`GetContext`. Another owner's task answers as if it did not
exist. Two callers may present the same `contextId`; their tasks and state
stay apart. Each request path carries its own `ServerCallContext` down to the
store; the server refuses to run one without it rather than treat it as
unscoped. The token is a header, so every method names its caller the same
way, whether or not it has `params.metadata`.

### `context=None` in the stores

The stores' `context` parameter keeps a2a-sdk's `TaskStore` signature.
`context=None` is deliberate unscoped access for Python code that holds the
store itself: `get`, `list`, the context listings, cancel and `delete` then
reach every owner's tasks of this agent.

A write never goes unowned. A task's owner is fixed by its first write, and
only a context names one:

| `save(task, context)` | New task | Existing task |
|---|---|---|
| a user's context | owned by the resolved user | saved if it is the recorded owner, else `TaskOwnerMismatchError` |
| `ServerCallContext()` | owned by the anonymous owner `""` | as above, with owner `""` |
| `None` | `TaskOwnerUndefinedError` - nothing is written | saved, keeping the recorded owner |

Context listings (`get_context_tasks`, `get_context_last_task`,
`get_context_ids`) are ordered by task creation, newest first, across owners;
an update keeps a task's place.

The owner here is the caller. It is unrelated to the lease owner of task
ownership in the PostgreSQL deployment - the server process currently
executing a task - which decides who may write a task's progress, never who
may see or cancel it.
