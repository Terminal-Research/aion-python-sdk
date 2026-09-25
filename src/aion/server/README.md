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
always name the same owner. A custom resolver is passed to the agent:
`AionAgent.from_adapter(..., owner_resolver=...)`; it should derive the owner
from the verified user.

### Where the user comes from

a2a-sdk's `DefaultServerCallContextBuilder`, which the JSON-RPC route uses,
takes the user from `request.scope["user"]` - the trusted slot an
authentication middleware such as Starlette's `AuthenticationMiddleware`
fills - and otherwise gives the request an unauthenticated user, whose name
is `""`. `AppFactory` installs no authentication, so a stock `aion serve`
sees every caller as that anonymous user: all clients are one owner and see
each other's tasks. **Isolation between users requires the application to
put a verified user into `ServerCallContext`**, by installing authentication
that sets `request.scope["user"]` before a request reaches the JSON-RPC
route. The SDK does not read identity from request headers or parameters.
`ServerCallContext.tenant` is not a source either: the JSON-RPC dispatcher
copies it from the request's own `tenant` field, which the client chooses,
and the default resolver ignores it.

### What is isolated

With a verified user, every A2A operation is limited to the caller's owner,
on the in-memory and the PostgreSQL store alike: `SendMessage` and
`SendStreamingMessage` (including a `taskId` to continue, and the
interrupted task found through a `contextId`), `GetTask`, `ListTasks`,
`CancelTask`, `SubscribeToTask`, and Aion's `GetContexts`/`GetContext`.
Another owner's task answers as if it did not exist. Two clients may present
the same `contextId`; their tasks and state stay apart. Each request path
carries its own `ServerCallContext` down to the store; the server refuses to
run one without it rather than treat it as unscoped.

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

The owner here is a user. It is unrelated to the lease owner of task
ownership in the PostgreSQL deployment - the server process currently
executing a task - which decides who may write a task's progress, never who
may see or cancel it.
