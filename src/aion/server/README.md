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
mounts them on the application it builds. `aion.yaml` declares agents and the
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

The server names the caller of every JSON-RPC request before the request
reaches the dispatcher. `CallerIdentityMiddleware`, which `AppFactory`
installs, reads the [distribution
payload](https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0) a
platform request carries in `params.metadata` and installs the caller the
way Starlette's authentication does: a `DistributionCaller` as
`request.scope["user"]` and credentials without scopes as
`request.scope["auth"]`. a2a-sdk's `DefaultServerCallContextBuilder` makes
them `ServerCallContext.user` and `state["auth"]`, and `resolve_user_scope`
makes the user's name - the distribution's id - the owner. The policy that
names the caller is `aion.server.identity.resolve_distribution_caller`. So
there are two callers:

- a request that carries a distribution payload belongs to that
  distribution. An agent published on several channels - an A2A endpoint, a
  Slack workspace - has an owner per channel, each with its own tasks and
  framework state;
- a request without one belongs to the anonymous owner `""`, shared by every
  such caller - a direct A2A client, `aion chat`, another agent calling this
  one - and, on a database several deployments share, by every deployment of
  the same agent id.

A distribution payload decides the caller whatever user and credentials a
middleware in front of `CallerIdentityMiddleware` set: both are replaced, so
a distribution never carries another caller's credentials. A request without
a payload keeps the scope as it found it. A malformed payload, an empty
distribution id included, is refused as an invalid request (`-32600`) rather
than served anonymously; the error names the fields at fault, never their
values. Headers name nobody. `ServerCallContext.tenant` is not a source either: the JSON-RPC
dispatcher copies it from the request's own `tenant` field, which the client
chooses, and the default resolver ignores it.

**This is isolation, not access control.** The distribution id comes from the
request itself, and nothing verifies that the platform sent it, so the caller
is never authenticated (`is_authenticated` is `False`). What the SDK serves
only to an authenticated user stays closed: Aion's `GetContexts` and
`GetContext` answer with empty projections, and an interrupted task is not
found through its `contextId` - it is continued by its `taskId`.

### What is isolated

Every A2A operation is limited to the caller's owner, on the in-memory and
the PostgreSQL store alike: `SendMessage` and `SendStreamingMessage`
(including a `taskId` to continue), `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask`, the push notification config methods, and Aion's
`GetContexts`/`GetContext`. Another owner's task answers as if it did not
exist. Two callers may present the same `contextId`; their tasks and state
stay apart. Each request path carries its own `ServerCallContext` down to the
store; the server refuses to run one without it rather than treat it as
unscoped.

A caller is named only by a request that has `params.metadata`: in A2A v1
that is `SendMessage`, `SendStreamingMessage` and `CancelTask`; in A2A v0.3,
`message/send`, `message/stream`, `tasks/get`, `tasks/cancel` and
`tasks/resubscribe`; and Aion's `GetContexts`/`GetContext`. A2A v1's
`GetTask`, `ListTasks`, `SubscribeToTask` and push notification config
methods have no such field - a request that adds one is refused as invalid
params - so they always run as the anonymous caller and never reach a
distribution's tasks.

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
