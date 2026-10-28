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
mounts them on the application it builds. Those routes are the application's:
the server's authentication leaves them alone - it neither asks for a token
there nor installs a caller - so an application that needs to know who calls
them authenticates the request itself. A router with a path the server
already serves is not mounted, and a request on a path the server routes to
its own endpoint stays protected.
`aion.yaml` declares agents and the
MCP proxy, and rejects any other key - see the [AppRegistry
guide](https://docs.aion.to/sdk/python/extensibility/app-registry).

## Who a task belongs to

Every task belongs to one owner: the caller who started it. The owner is
what `AionAgent.owner_resolver` names for a request's `ServerCallContext` - by
default a2a-sdk's `resolve_user_scope`, the user's name. `AppFactory` builds
the task store with that same resolver and refuses to start if a store was
built with another, so the tasks table's `owner_scope` and the task's push
notification configs always name the same owner.

The framework state behind the tasks - LangGraph's checkpoint `thread_id`,
ADK's session `user_id` - belongs to the conversation (`StateScope`). A
private conversation is its caller's, and its state owner is that same owner.
A conversation Aion routes through its gateway, named by a verified
invocation token, is shared: its state is keyed by the receiving agent
identity and the edge environment (`aion.gateway:<identity>:<edge>`), so every
participant's turn sees the same memory, while each task - reading,
continuing, cancelling it, its push configs - stays its initiator's. Context
admission keeps a context private or shared, never both (see below).
A custom resolver is passed to the agent:
`AionAgent.from_adapter(..., owner_resolver=...)`; it receives the request's
`ServerCallContext`, whose user is the caller described below.

### Where the user comes from

Every request to an agent's server needs a verified caller - the JSON-RPC
endpoint, the OpenAPI schema and any route the SDK or a plugin adds alike.
Only the public paths are open without one: the agent card, health and the
configuration schema, which a client or a probe reads before it has a caller.
Routes an application adds through `AppRegistry` follow the application's own
policy (see above). The agent card declares the bearer scheme in its
`securitySchemes`; Aion's tokens are issued by the Aion control plane. A
request without a verified caller is answered `401` before its body is read,
and the agent never runs.

A refusal is always `401` with an RFC 6750 `WWW-Authenticate: Bearer`
challenge (`error="invalid_token"` when a token was presented), or `503` with
`Retry-After` while the verification keys have not loaded. Its body is written
in the error format of the transport the path belongs to:

| Path | `401` body | `503` body |
| --- | --- | --- |
| JSON-RPC endpoint | JSON-RPC error `-32051` `Unauthorized`, `data.detail` | JSON-RPC error `-32603`, `data.detail`, `data.retryable: true` |
| Context HTTP+JSON routes | `application/problem+json`, `title` `Unauthorized`, `detail` | `application/problem+json`, `status` `503`, `detail` |
| Anything else | `{"error": "unauthorized", "detail": ...}` | `{"error": "unavailable", "detail": ...}` |

The JSON-RPC error answers `id` `null`, since the body is never read.
`-32051` (`AuthenticationRequired` in `aion.server.core.errors`) is returned
for every JSON-RPC method, not only the Context ones. It is Aion's own code:
Aion assigns its JSON-RPC errors from `-32050` to `-32099`, while a2a-sdk
assigns its own upward from `-32001`, and the Context extension's table lists
`-32010` for missing authentication - the next code a2a-sdk would take. The
problem
details carry no `type`, which the specification does not define for these
statuses.

Where the caller may come from is the server's mode:

| Mode | Selected by | Served |
| --- | --- | --- |
| Hosted | `DEPLOYMENT_ID` set | invocation tokens only |
| Strict | `AION_REQUIRE_INVOCATION_AUTH=true`, not hosted | invocation tokens only |
| Remote or local | neither | anonymous session tokens; invocation tokens when `AION_CLIENT_ID` is set; the application's own authenticated user |

The bearer token chooses the path by what it claims to be, never by whether a
check failed. A token with an Aion `typ` header is verified in full; if it
does not verify the request is refused, whoever else the request could have
been served as. A token without an Aion `typ` but with an Aion `token_use` is a
damaged Aion token and refused. Anything else - no token, or the
application's own credential - is served as the user an authentication
middleware of the application installed in front of `AionAuthMiddleware`,
when the mode allows it and that user `is_authenticated` with a non-empty
`display_name`. The name becomes the owner of the request's tasks and state as
it is: keeping it unique, and apart from Aion's `aion:v1:` subjects and the
`aion.gateway:` keys of shared conversation state, is the application's
contract - the SDK adds no prefix, and two equal names are one owner. The
name has to be stable from request to request and non-empty; it need not be a
UUID or have the shape of an Aion principal. Such a user is a private caller
with no gateway rights. A token stripped of both its `typ` and its
`token_use` cannot be told from an application's credential; whatever the
application's authentication makes of it, it carries no Aion rights.

Any bearer token longer than 8 KiB (8192 bytes of UTF-8) is answered `401`
before anything reads or routes it - an application's own credential too, and
its user is not served instead. An application whose credentials can be
longer has to keep them out of the `Authorization: Bearer` header of requests
to the agent's server.

`AppFactory` builds a `TokenVerifier` (`aion.server.auth.build_token_verifier`)
and hands it to `AionAuthMiddleware`, which it installs outside the server's
other middlewares. The middleware installs the caller the way Starlette's
authentication does: an `AuthenticatedCaller` as `request.scope["user"]` and
`authenticated` credentials as `request.scope["auth"]`. a2a-sdk's
`DefaultServerCallContextBuilder` makes them `ServerCallContext.user` and
`state["auth"]`, and `resolve_user_scope` makes the token's `sub` the owner.
The caller is typed: its `principal` (type and ID), `credential` (invocation
or anonymous session), `assurance` - what Aion established about it, so a
verified session or provider sender is never taken for an account login - and,
for an invocation, the `gateway` coordinates it was routed through. The
distribution a request came through is its channel, not its owner. A verified
Aion token replaces whatever user and credentials a middleware in front of
`AionAuthMiddleware` set.

Both kinds of token are ES256-signed JWTs with the one key Aion publishes; the
protected `typ` header tells them apart.

| | Invocation token | Anonymous session token |
| --- | --- | --- |
| `typ` | `aion-invocation+jwt` | `aion-session+jwt` |
| `aud` | exactly this server's `AION_CLIENT_ID` | exactly `urn:aion:a2a:anonymous-session` |
| `token_use` | `a2a_invocation` | `anonymous_session` |
| `sub` | the initiating principal | an `AnonymousSession`, its ID the session UUID |
| `assurance` | `account`, `runtime`, `provider`, `session`, `internal` or `unattributed` | `session` |
| Gateway claims | `owner_agent_identity_id`, `edge_agent_environment_id`, `terminal_agent_environment_id`, lowercase UUIDs | none |
| Lifetime (`exp` − `iat`) | 1 hour | 30 days |
| Server deployed by the Aion platform | accepted | refused |
| Any other server | accepted when `AION_CLIENT_ID` is set | accepted |

Both also need `iss` = `AION_API_CLIENT_AUTH_ISSUER` (default `aion.io`),
`contract_version` = 1, and integer `iat` = `nbf`. `sub` is canonical,
`aion:v1:<PrincipalType>:<unpadded base64url of the ID>` (`aion.server.auth.Principal`),
with a non-empty ID of at most 1024 bytes of UTF-8. The ID formats the
contract fixes are checked: `AnonymousSession` and `ExternalIdentity` IDs are
lowercase UUIDs, and an `ExternalSender` ID is
`v1:<provider>:<encoded-tenant>:<encoded-sender>` - a lowercase provider label,
then the provider's tenant and sender IDs, each unpadded base64url of its exact
UTF-8. The tenant is part of the sender's identity; whether the tenant exists
and the sender belongs to it is Aion's to establish before it signs, and the
SDK does not ask the provider. Any other type's ID is opaque and
case-sensitive: the agent and system principal types and their formats are
Aion's backend contract, not a closed list in the SDK. Assurance is read from
its own claim, never inferred from the principal type; the one rule tying
them is that `session` assurance belongs to an `AnonymousSession` principal
and no other. The header carries `alg`, `typ` and `kid` and nothing else -
`jku`, `x5u`, `jwk`, `crit` and `zip` are refused - and no member appears
twice in the header or the claims. Times are checked with 30 seconds of
tolerance; nothing else is. A kind a server does not accept is refused before
any key is looked up, and the reason in the `401` never quotes the token.

A server is hosted when `DEPLOYMENT_ID` is set. It has to be the deployment's
UUID: a malformed or empty value stops the server instead of being read as
"not hosted". A hosted or strict server without `AION_CLIENT_ID` does not start
either.

The keys are the JWKS at `{AION_API_HOST}/runtime/a2a/verification-keys`, a
public endpoint that needs no credentials (`JwksKeySource`). It has to be
HTTPS, or plain HTTP to a loopback address for development:

- The keys load at startup and are kept for the life of the process: no timer,
  no refresh. A key endpoint outage after that changes nothing.
- Only EC P-256 keys for ES256 are kept, without a private part, and only under
  a `kid` that is the key's RFC 7638 thumbprint. A `kid` published twice is
  ambiguous and used for neither. A token whose `kid` is not loaded is refused
  without a fetch.
- A fetch has 5 seconds in total, reads at most 64 KiB and follows no
  redirect.
- Until keys load, a request is answered `503` with `Retry-After`, never let
  through unchecked. It may start a new fetch, at most one a second; concurrent
  requests share it. The first failure logs a warning, repeats log at debug.

The startup log says whether the server is hosted on the Aion platform.

Other headers name nobody. `ServerCallContext.tenant` is not a source either:
the JSON-RPC dispatcher copies it from the request's own `tenant` field, which
the client chooses, and the default resolver ignores it. There is no `/docs` or
`/redoc`.

Finding an interrupted task through its `contextId` goes through the same
owner filter as everything else: a caller continues its own interrupted task,
and only one of the executor its message addresses - an executable
extension's request a task of that extension, any other message a task of the
agent's framework; a waiting task of another executor keeps waiting and the
message starts a new task.
The Context extension (`GetContexts`, `GetContext`, `DeleteContext`) answers
by context binding instead - see [Contexts](#contexts) below. Only a call
without a `ServerCallContext` - from Python code that holds the handler -
reads no history at all.

### What is isolated

Every A2A operation is limited to the caller's owner, on the in-memory and
the PostgreSQL store alike: `SendMessage` and `SendStreamingMessage`
(including a `taskId` to continue), `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask` and the push notification config methods. Another owner's
task answers as if it did not exist. The Context extension's methods are
limited to the contexts the caller is bound to. Each request path carries its own `ServerCallContext` down to the
store; the server refuses to run one without it rather than treat it as
unscoped. The token is a header, so every method names its caller the same
way, whether or not it has `params.metadata`.

An invocation with `unattributed` assurance has no individual access. Its
subject is attribution Aion keeps for audit - the common `ExternalAnonymous` -
shared by everyone who arrived anonymously, so it tells no initiator from
another (`aion.server.auth.has_individual_access`). It starts a task in its
gateway conversation and follows it on the same request; every later reach is
refused as if no task existed: `GetTask`, `CancelTask`, `SubscribeToTask`,
continuing a task, the push notification config methods and finding an
interrupted task through its `contextId`. `ListTasks` and `GetContexts` answer
empty, `GetContext` answers `ContextNotFound`, and `DeleteContext` changes
nothing.

### Who may use a context

A context ID is chosen by the client, but the framework state keyed by it -
LangGraph checkpoints, ADK sessions - outlives every task in it. So before a
message does anything, its caller is admitted into the context
(`aion.server.tasks.admission`). The first message reserves the context for
its holder; a message from anyone else is answered `TaskNotFound`, the same
error as for a task that does not exist, whoever holds the context:

| Holder | Who | Shares the context with |
| --- | --- | --- |
| private | an anonymous session, the application's own user | nobody: its owner scope alone |
| shared | a verified Aion invocation | every invocation of the same gateway conversation: the same receiving agent identity and edge environment |

A context is one or the other, never both, and its holder never changes.
Admission comes first in `SendMessage` and `SendStreamingMessage`, ahead of
extension verification, preprocessing (file uploads), push configuration and
the task, so a refused message leaves nothing behind - no task, no push
config, no reservation - and every executor runs behind it. A
message that continues a task takes the task's context: the task has to be
the caller's own, and a `contextId` sent with it has to match. A message with
neither gets a new context ID, reserved like any other. In a shared context
each participant still owns only their own tasks.

A reservation is not released when the agent fails, when the process dies or
when task rows are deleted, because the framework state may still be there.
Only `DeleteContext` ends it, once that state is gone too - see
[Contexts](#contexts). Reservations live beside the task store: in PostgreSQL
(`context_reservations`, keyed by agent and context, so several servers of
one agent agree and several agents share a database) or in the process for
the in-memory store. An agent that brings its own checkpointer or session
backend outside the SDK's database keeps that state's lifetime in step
itself.

#### Contexts from before reservations

A database that already holds conversations when reservations are introduced
(migration `006`) has nothing that says who may continue them: owners were
recorded per task, and a shared conversation's gateway coordinates not at
all. So every existing context is closed - reserved `blocked` for its agent,
admitted into by nobody - whoever its tasks name and however many there are.
Its data is found in the tasks table, in the LangGraph checkpoint tables
(`aion_langgraph`) and in the ADK session tables (`aion_adk`), for the
frameworks whose tables the database has. Nothing is moved or deleted: the
old tasks stay readable by their owners through the usual owner filter, and
the old framework state stays where it was. Only new messages into an old
context are refused, with the same `TaskNotFound`; new context IDs work as
always.

State saved before it carried the agent - a LangGraph checkpoint whose
`thread_id` is the context ID alone, an ADK session under the shared
`default-user` - names no agent to reserve for. The first message into such
a context is refused instead, whatever agent it is for, before the context is
reserved and before any task, upload or push config. Nothing a current server
writes takes that shape, so two servers reserving a new context cannot
disagree about it.

Upgrading follows one order: stop every process of the previous version,
run the migration (`aion db migrate`, or a server starting against the
database with `DB_MIGRATE_ON_START`), then start the new version. An old process still writing during or after the
migration creates tasks and state no reservation knows about, and the check
at first use cannot make up for it.

### Contexts

The Context extension (<https://docs.aion.to/a2a/extensions/aion/context/1.0.0>)
lets a client that talks to this server directly list, read and delete its
conversations. It is declared on the agent card and needs no activation. The
three methods are served over JSON-RPC by name and over HTTP+JSON by path,
relative to the A2A endpoint:

| Method | HTTP+JSON | Result |
| --- | --- | --- |
| `GetContexts` | `POST /contexts:get` | the caller's contexts, most recently active first |
| `GetContext` | `POST /context:get` | one context: chronological messages, up to 50 latest artifacts, latest status |
| `DeleteContext` | `POST /context:delete` | `{"contextId": ...}` |

A request without a verified caller is refused before any method runs: `401`
with JSON-RPC error `-32051` on the JSON-RPC endpoint, and `401`
`application/problem+json` on the HTTP+JSON routes (see *Where the user comes
from*).

**Bindings.** Admission binds every caller it admits to the context, by owner
scope (`context_bindings` in PostgreSQL, the reservation entry in memory). A
private context has one binding, a shared gateway conversation one per
participant. A caller sees a context exactly while it is bound to it; any other
context - another caller's, or one that does not exist - is answered alike:
`ContextNotFound` (`1000`, HTTP `404`) for a read, success for a delete. A
caller without individual access is never bound. `GetContext` returns every
participant's messages, not only the caller's, because a binding is to the
whole conversation. Pagination selects the newest `historyLength` messages
after skipping `historyOffset` and returns them in order. `title` and
`summary` are always `null`: this server generates neither.

**Deletion.** `DeleteContext` removes the caller's binding. While another
caller is bound, that is all. Removing the last one makes the deletion durable:
the reservation becomes `deleting`, and no message is admitted into the context
until it ends. Then every active task of the context, whoever owns it, is
cancelled through the ordinary cancellation path, in groups of ten; once all
are terminal the framework state - LangGraph checkpoints, ADK sessions - is
deleted under the context holder's state scope, and the tasks, their messages,
artifacts, versions, journal events (`task_events`) and push configurations,
the ADK artifact version numbers given in the context and the bindings are
removed in one storage step. The reservation becomes `deleted`, and the next
message with that context ID reserves it afresh: a new conversation, with
nothing of the old one.

**Retention.** A context keeps its whole history for as long as it lives:
every task, with its messages, artifacts, versions and journal events, and
every push configuration. Nothing expires or is pruned on a schedule, so a
long-lived context grows with every task in it. Deleting the context is what
removes all of it.

A deletion that cannot finish in one request - a cancellation still settling
on another server, a database error, a concurrent attempt at the same deletion
- answers `ContextDeletionInProgress` (`1001`, HTTP `409`, retryable, with a
`deletionOperationId`) and stays durable. The same caller repeating the
request resumes it, and a server resumes every pending deletion of its agent
in the background when it starts. A definitive refusal - a cancellation the
agent rejects, or framework state its executor cannot delete
(`AgentFrameworkExecutor.supports_state_deletion`, asked before any task is
cancelled) - returns the context to `active` without the caller's binding and
answers `ContextNotDeletable` (`1002`, HTTP `409`).

Nothing the deletion removed comes back. A new task in a context that is
`deleting` or `deleted` is refused when it would first be written - a message
admitted just before the deletion started included - with
`ConcurrentTaskModificationError`; in PostgreSQL that check holds the
reservation row, so it and the end of a deletion cannot interleave. A late
write of a removed task is refused too: in PostgreSQL its version row is gone,
so the write is stale, and as a new task it is refused by the check above; in
memory the task is remembered as retired. A stream still open on a
task cancelled by the deletion closes with the outcome the task declared,
since the task itself is no longer stored.

Limitations, as implemented:

- A `blocked` context (from before reservations) has no holder to key its
  framework state by: its tasks are deleted, its framework state is not, and
  the context stays closed to every caller.
- A framework state store outside the SDK's own backends is deleted only if
  the executor can do it; with a LangGraph checkpointer without
  `adelete_thread`, removing a context's last binding answers
  `ContextNotDeletable`.
- Unattributed invocations are never bound, so their tasks in a shared
  conversation are deleted with it but listed and read by nobody.
- A removed participant of a shared gateway conversation is bound again by
  its next message into it, which is a new authorized message rather than a
  restored grant.

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

Context listings (`get_context_tasks`, `get_context_last_task`) are ordered by task creation, newest first, across owners;
an update keeps a task's place.

The owner here is the caller. Which server process executes a task plays no
part in who may see or cancel it.

## A2A methods: what a client sees

The standard A2A methods are a2a-sdk 1.2's, and Aion's handler adds to them
rather than replacing them. A client sees a2a-sdk's behaviour except where
this section says otherwise.

- **A message to a task that already has an outcome** is refused with
  `UnsupportedOperationError` (`-32004`), naming the state:
  `Task <id> is in terminal state: TASK_STATE_COMPLETED`.
- **`CancelTask`** answers with the task's outcome: `CANCELED`, or the
  outcome its running turn reached in the meantime. It activates none of the
  extensions named in `A2A-Extensions`, nor does `GetTask`. On a task that
  already has an outcome it answers `TaskNotCancelableError` (`-32002`); a
  missing task, or another caller's, answers `TaskNotFoundError` (`-32001`).
- **`SubscribeToTask`** on a task that already has an outcome answers
  `UnsupportedOperationError` (`-32004`), as in a2a-sdk; the result is read
  with `GetTask`.
- **`message/send`** always answers with a `Task`, never a bare `Message`,
  and every stream - `SendStreamingMessage` and `SubscribeToTask` alike -
  closes with the stored `Task`, where a2a-sdk may close one with the last
  status update.
- **`CreateTaskPushNotificationConfig`** answers with the config as stored: an
  `id` the request left empty is the task's id.
- **Params** fields a method does not define are ignored, for the standard
  methods and the Context extension's alike. A request whose params do not
  parse answers `-32602`; its `error.data` is a list holding a
  `google.rpc.ErrorInfo` detail (`reason` `INVALID_PARAMS`, `domain`
  `a2a-protocol.org`) with the reason in `metadata.parseError`.
- **The Context extension's methods** (`GetContexts`, `GetContext`,
  `DeleteContext`) check `A2A-Version` as the standard methods do: another
  major version than 1 answers `VersionNotSupportedError` (`-32009`). A call
  without the header is served, where a standard method reads it as 0.3 and
  refuses it. A response carries the Context extension's URI in
  `A2A-Extensions`.
- **`ListTasks`** page tokens are opaque cursors naming a position in the
  listing, so a token stays valid when the task it names changes or is
  deleted. A token that is not such a cursor answers `-32602`, and the
  PostgreSQL store also refuses one issued for different filters.
- **The agent card** carries a weak `ETag`; a request with a matching
  `If-None-Match` answers `304`.
- **Push notifications** carry the config's `token` as
  `X-A2A-Notification-Token` and its `authentication` as `Authorization:
  <scheme> <credentials>`; credentials without a scheme are sent as
  `Bearer`.
- **Push notification URLs** on a server the Aion platform hosts
  (`DEPLOYMENT_ID` set) must be `http` or `https` and resolve to public
  addresses only: a config naming another one - loopback, private,
  link-local, the cloud metadata endpoint - is refused with `-32602`,
  created on its own or inline in `SendMessage`, and a delivery to a stored
  one is skipped. Elsewhere any URL is accepted.

### How a client receives a task's updates

A run belongs to the server, not to the request that started it: it goes on
when the client disconnects, and stores its outcome either way. A client
follows it through one of the methods below. *Durable* is what the task
stores - status changes with their messages, artifacts, the outcome.
*Ephemeral* is live-only progress the task never stores: response and
thinking deltas, ephemeral messages and reactions (see [Transient stream
artifacts](#transient-stream-artifacts)), and status updates flagged
`aion:ephemeral`.

| Method | How it is delivered | What arrives | Connection held |
|---|---|---|---|
| `SendStreamingMessage` | SSE on the request, opened and closed by the stored `Task` | everything, ephemeral included | until the task has an outcome or waits for input |
| `SendMessage` | one JSON response: the `Task` once it has an outcome or waits for input; with `configuration.returnImmediately`, the task at once while the run goes on | durable only | as for the stream; none with `returnImmediately` |
| push notifications | `configuration.taskPushNotificationConfig` on a send, or `CreateTaskPushNotificationConfig`; the server POSTs each change the task stores to the webhook, an outcome or a wait for input as the full `Task` | durable only | none |
| `SubscribeToTask` | SSE: the stored task, then the live events until the task stops; a task that already has an outcome is refused | on the server running the task, everything; on another server the task's journal, so durable only | until the task stops |
| `GetTask` | one JSON response: the stored task, its history cut to `historyLength` | durable only | none |

Push carries every outcome the task stores, including those the run did not
write itself: a cancel written by another server, the `FAILED` of an agent
that raised, a task settled at shutdown or as abandoned. Deliveries are made
one at a time from the task's event loop, each after its change is stored, so
a webhook that answers slowly holds back the delivery of the run's later
events, on every channel, for up to `PUSH_NOTIFICATION_TIMEOUT_SECONDS`
(default 5 s) per delivery.

A stream that closes on a `Task` that is still `SUBMITTED` or `WORKING`, and
a `SendMessage` answered with one, did not see the run end: a2a-sdk drops a
subscriber that falls 1024 events behind (it logs `Evicting event queue
sink`), and a stopping server closes its connections. Resubscribe with
`SubscribeToTask`, or poll `GetTask`, for the outcome.

### Several servers of one agent

With PostgreSQL the server runs a2a-sdk's cluster mode, so any number of
servers of one agent can share the database. The rules are a2a-sdk's:

- Any server executes any request it receives. Each task has a version in
  `task_versions`; every write names the version it was derived from, and a
  stale one is refused. The event of each write goes to `task_events` in the
  same transaction.
- A subscriber on a server that is not running the task is served the stored
  task and then the task's journal, polled every 0.5 s, until the task stops
  again. Live-only updates - response and thinking deltas, ephemeral
  progress (see [Transient stream artifacts](#transient-stream-artifacts)) -
  are never stored, so they reach only a subscriber on the executing server.
- The next turn of a paused task runs on whichever server receives it, from
  the stored task. Framework state has to be in the same database for that:
  the SDK's PostgreSQL checkpointer and session service are.
- Nothing stops two servers from executing one task at once - a second
  message sent to another server while the first runs is executed there. The
  version decides what lands: a write over a task that another execution has
  already finished is dropped, and that execution stops. An executable
  extension's task is the exception by default: a message into its running
  task is refused with `task_busy` on any server, unless its executor
  queues one (`message_to_busy_task = "queue"`). An extension that runs one task
  per context - behavior evolution - rejects a second one with `context_busy`
  on any server too, because its check reads the database.
- A task routed to an executable extension is served by that extension only. A server
  without it - another image during a rollout - refuses a message into the
  task with `extension_unavailable` and leaves the task as it was.
- `CancelTask` for a task this server holds cancels it through its executor.
  For any other task it writes `CANCELED` over the stored version, sends its
  push notification and answers at once; the server executing the task stops
  it at its next write, without calling `AgentExecutor.cancel` there; the turn
  stopped that way asks its executor to stop (`TaskExecutor.cancel`).
- A server that dies leaves its tasks in the state they had. Any server can
  cancel such a task, and any server settles it once it has gone without a
  durable write for longer than `AION_STALE_TASK_TIMEOUT` (seconds, default
  3600, `0` turns it off): `FAILED` with
  `aion:settledReason=task_abandoned`, and for an executable extension's
  task the error `task_abandoned` (retryable) as the extension's failure
  carries it. Every
  server runs the sweep, one at a time per agent under a lock, every 60
  seconds or every half of the timeout when that is shorter, and a message
  into such a task settles it first. A server never
  settles a task whose turn runs in its own process - it logs a WARNING for
  one its sweep finds - and never a task waiting for input. The write is
  versioned: a task written in the meantime keeps its state, and an
  execution still alive somewhere stops at its next write, as after a remote
  cancel. Keep the timeout above the longest a turn of the agent goes
  without writing a status or an artifact.
- An orderly shutdown stops accepting requests, then asks every turn it runs
  to stop (`TaskExecutor.cancel`) and waits together for the ones whose
  executor can stop them: the server closes such a turn's task `FAILED` with
  `server_shutdown`, and a stream open on it receives that outcome. uvicorn then waits for the
  connections still open, so a stream on any other task does not hold the
  shutdown, and the tasks the server was still running are settled as
  `FAILED` with `aion:settledReason=server_shutdown` and pushed; a webhook
  that has not answered by the end of the settlement's share is not waited
  for. `AION_SHUTDOWN_TIMEOUT`
  (seconds, default 25) bounds all of it: `aion serve` kills an agent that
  has not stopped by then, and the agent divides it among the steps
  (`aion.server.shutdown`) - by default 15 s for the running turns, which
  `TaskExecutor.cancel` receives as `within`, 5 s for open connections, 2 s for
  the settlement and 3 s for the rest. Set it a few seconds below the stop
  timeout of the platform running the server - Kubernetes
  `terminationGracePeriodSeconds`, `docker stop -t` - so the server's own
  shutdown finishes first.
- The journal keeps every event of a task for as long as the task exists.
  Deleting the task, or its context, removes its events in the same
  transaction as the task.

Without a database the store is in memory and the server is a2a-sdk's single
process: no versions, no journal, nothing shared, so one server only.

## Callback execution scope

The executor builds callback attribution from the current accepted request, not
from saved task history. An activated usage-attribution carrier stays opaque and
is forwarded unchanged. Otherwise the verified caller's canonical subject,
including an anonymous session, becomes `DirectAttribution`. Application
authentication can report a canonical user name; a raw name has no Aion namespace
and is attributed as `ExternalAnonymous` without changing its task ownership.
This reported value does not grant permissions or choose the payer.

The ContextVar scope covers stream consumption, resume and cancellation, then
restores its parent even on failure. Async work spawned inside it inherits that
invocation's scope. Work transferred to another process must establish a fresh
execution scope; never checkpoint a bearer or restore attribution from framework
state. A supplied-but-unactivated or invalid usage carrier is an error, not a
signal to fall back to direct mode.

## Response Extension Provenance

A2A 1.0 JSON-RPC responses acknowledge verified invocation extensions in the
`A2A-Extensions` header, including empty or delayed streams. Unknown declarations
are not acknowledgment. Runtime-produced agent messages retain the same URI list
through persistence; earlier history and user messages are not relabeled. This
does not require copying extension payloads into response metadata. The A2A
methods are served over JSON-RPC only; the Context extension adds its own
HTTP+JSON routes (see [Contexts](#contexts)).

### Transient stream artifacts

The task manager delivers `aion:stream-delta`, `aion:thinking-delta`,
`aion:ephemeral-message`, and `aion:reaction` updates to live clients without
adding them to stored task artifacts, and skips status updates flagged
`aion:ephemeral` the same way. Push notifications skip both, by the same
predicate (`is_ephemeral_event`): a webhook receives what the task stores.
The deduplicator does not retain their IDs, so repeated chunks remain
deliverable. Ordinary artifacts still persist. This filter does not remove
artifacts from previously stored tasks.


## Server and background logging

Applications use ordinary Python loggers. The server's root handlers write to
its console and, when `LOGSTASH_HOST` and `LOGSTASH_PORT` are configured, to
Logstash. `LOG_LEVEL` defaults to `INFO`; Logstash excludes debug records and
records without a running version, invocation distribution, or trace.

The proxy stays silent for HTTP 200 responses to `/health/` and `/health/system/`.
Other requests are logged at debug level, or warning for proxy-owned failures,
with the method, path, status, client address, and supplied `Host`, `User-Agent`,
`X-Forwarded-For`, and `X-Real-IP` headers.

`VERSION_ID` identifies startup, shutdown, scheduled jobs, and background threads
without requiring an incoming request. Within a request, its version and tracing
context take precedence. Logs emitted before a version is supplied or resolved
remain on the console unless another accepted identity is present; they are not
buffered for later attribution. Ordinary library and root log records receive
the same optional context fields as Aion records.

The agent process logs `Agent server starting` after configuring its handlers.
`Agent server ready; accepting requests` follows completed application startup
and socket binding. A failed lifespan emits a failure event instead. These events
use the SDK logger, independently of the suppressed Uvicorn INFO messages.

Each `aion serve` launch opens an `application.startup` trace before loading
the application. Its agent processes use `agent.startup` child spans and its
proxy uses `proxy.startup`. Process launches carry W3C trace context explicitly;
each child keeps the trace ID and generates its own span ID. Log records expose
`trace.id`, `span.id`, `span.name`, and `parent.span.id`, together with the
existing deployment, organization, version, host, and process identifiers.
Database preparation, plugin initialization, agent construction, application
assembly, and lifespan startup have their own phase spans. Awaited operations
and migration work dispatched with `asyncio.to_thread` retain that context.

Readiness is acknowledged after lifespan initialization and socket binding.
Startup spans finish at readiness or failure; the launcher records a failed
startup if any required child fails. With `--startup-timeout 0`, the launcher
records `application.startup launched`, without claiming readiness, while each
child still records its own eventual outcome. A later managed-process restart
captures fresh launch context instead of replaying the original carrier.

Requests, the platform connection, and ongoing background maintenance do not
inherit startup tracing. Database pool workers start without tracing context;
the awaited pool readiness check retains the initialization span. Agent and
proxy lifespan failures log their cause before leaving their startup span.
Code that launches long-lived background tasks during
initialization should also detach the startup context for those tasks; ending
a span alone does not clear the context a task inherited. Tracing initialization
preserves an application-configured OpenTelemetry provider. The SDK supplies
log correlation by default; it does not configure a span exporter or connect
startup to the backend deployment trace.

An independently launched child process must configure its own logging or relay
structured records to its parent. Environment inheritance alone does not carry
Python handlers into that process. Raw subprocess stderr is not a log protocol.
