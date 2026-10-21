# Executors and executable extensions

The server runs every task with an executor from one pool, `TaskExecutorPool`,
and calls each the same way, through `TaskExecutor`: `stream` for a new task,
`resume` for a message that continues one, `cancel` to stop its running turn.
An executor yields A2A events, which go the standard way
(`AionEventPipeline`); the turn around them is the server's. The pool holds
two kinds of executor:

- the agent's **framework executor** (`AgentFrameworkExecutor`), which the
  framework's plugin (LangGraph, ADK) creates from the agent's code. It
  serves every task no executable extension takes, and also owns the
  framework's state of a context, which `DeleteContext` removes;
- the executor of each **executable extension** the agent enables
  (`ExtensionExecutor`): an A2A extension whose own executor runs the task in
  place of the agent's framework. A request that activates it starts a task
  of its executor, and every later message and cancel of that task goes to
  the same executor.

The one executable extension the server ships is behavior evolution
(`BEHAVIOR_EVOLUTION_EXTENSION_URI_V1`): a thin wrapper in the SDK around the
evolution toolkit, a package of its own (`aion-toolkit-behavior-evolution`).
Nothing registers itself: the server ships the list of its executable
extensions.

| Where | What |
| --- | --- |
| `src/aion/server/agent/adapters/interfaces/executor.py` | `TaskExecutor`, `AgentFrameworkExecutor`, and what one execution is told (`ExecutionConfig`, `StateScope`). |
| `src/aion/server/agent/execution/pool.py` | `TaskExecutorPool`, the route of a new task kept on the call context (`EXTENSION_ROUTE_STATE_KEY`), and the JSON-RPC refusals `extension_unavailable` and `task_busy`. |
| `src/aion/server/agent/execution/request_executor.py` | `AionAgentRequestExecutor`: one path for every executor - the turn, its outcome, cancel, shutdown. |
| `src/aion/server/agent/execution/extensions/executable.py` | `ExtensionExecutor`, `ExecutableExtensionDescriptor`, `parse_options`. |
| `src/aion/server/agent/execution/extensions/registration.py` | `EXECUTABLE_EXTENSIONS`, `register_extensions` and `create_extension_executors`, the two steps of `AppFactory`. |
| `src/aion/server/agent/execution/extensions/model_service.py`, `secret_values.py` | Access to the Aion model service on a caller's behalf, and the deployment's secret values. |
| `src/aion/core/agent/conversation.py`, `src/aion/core/runtime/context/conversation.py` | The history of a turn's conversation, which every executor reads: its tasks, its generation, the versions of an artifact and the number a task writes one under. |
| `src/aion/server/tasks/conversation.py` | `StoredConversations`: the history of each turn over the server's stores, installed on the turn's runtime context. |
| `src/aion/server/agent/execution/extensions/evolution.py` | `EvolutionExecutor`, its options (`EvolutionOptions`) and its descriptor. |
| `src/aion/core/a2a/codes.py`, `src/aion/core/a2a/errors.py` | The codes the server reserves and the A2A shape of an extension's error; in core, so an executor that needs only the SDK base uses them. |
| `src/aion/server/tasks/context_artifacts.py`, `context_runs.py` | The readers of an artifact's versions, and of who holds a context, its generation and its tasks, over the in-memory store or PostgreSQL. |
| `tests/support/scripted_executor/`, `tests/support/executors.py` | The executors the SDK's own tests drive the server with, and a stand-in for the evolution toolkit; see [Testing](#testing). |

## Registration and creation

`AppFactory._initialize` takes two steps:

1. `register_extensions(agent_config)`, before the agent card exists: the
   registry is filled afresh with the protocol extensions of `aion.core`
   (`register_protocol_extensions`) and the executable extensions of
   `EXECUTABLE_EXTENSIONS`, and activates every URI `enabled_extensions`
   lists. It returns the enabled executable extensions with their options.
2. `create_extension_executors(enabled)`, once the agent is built: each
   enabled executable extension's executor is created with its options. The
   pool is the agent's framework executor plus these, and the request
   executor is built over it, with the history of each turn over the stores
   (`StoredConversations`).

An executor that cannot be created - options it refuses, a package, a
variable or a program it needs missing on this deployment - raises
`ConfigurationError` naming the extension and why, and the server does not
start. An extension the agent does not enable is registered and nothing
more: its executor is not created.

### Options in `aion.yaml`

An `enabled_extensions` entry is a URI, or a URI mapped to the options of an
executable extension:

```yaml
enabled_extensions:
  - "https://docs.aion.to/a2a/extensions/aion/daemon/1.0.0"
  - "https://docs.aion.to/a2a/extensions/aion/behavior/evolution/1.0.0":
      callers: [platform, anonymous_session]
```

An empty block (`{}` or nothing) keeps the defaults, like a bare URI. The
executor validates its options itself, usually with `parse_options(model,
uri, options)`: an option it does not take or a value it does not allow is a
configuration error naming the URI and the fields. Options given to a URI
that is not an executable extension of this server (any protocol extension)
are one too, and so is a URI listed twice. The options are the agent's own:
the card's `AgentExtension.params` stays empty.

An option is what the agent is - the same wherever it runs, reviewed with
its code. What differs from one deployment to another - where a call goes,
its credentials, paths - is the deployment's environment, which the executor
reads itself.

## An extension's executor

`ExtensionExecutor(options)` is created once and serves every task of its
extension, concurrently. `stream(context, config)` and
`resume(context, config)` get the a2a `RequestContext` - the message, the
call context with the caller (`verified_caller`), the task - and the
`ExecutionConfig` of the turn; the request's runtime context, with its
extensions, callback attribution and the history of the turn's conversation,
is current while the turn runs (`AionRuntimeContextRegistry`).
`cancel(config, within=)` stops the turn; see [Cancel](#cancel) and
[Stopping](#stopping).

Everything particular to the extension is the executor's own:

- **its payload** - read from the message with a `MessagesCollector` over its
  payload models;
- **who may call it, what it may run** - checked when the turn starts;
- **one task per context**, if it needs it - `conversation.tasks(unfinished=True)`
  lists the context's tasks without an outcome, oldest first, whoever owns
  them, and the executor picks its own by their route
  (`routed_extension_of`); the read holds across the servers of the agent,
  and nothing locks the context between it and the task's first write;
- **artifact versions** - see [Artifact versions](#artifact-versions);
- **a message into its running task** - `message_to_busy_task`, `"refuse"`
  for an extension: the server refuses it with `task_busy` before the
  task changes. An executor that sets `"queue"` gets the message as the
  task's next turn (`resume`), as a2a-sdk serves it: after the running turn
  on the server running it, alongside it on another server, where the task's
  version decides which writes land. The message never reaches the running
  turn.

An executor refuses its request by raising
`ExtensionRejection(code, message, retryable=...)` before it yields
anything; the server closes the task `REJECTED` with the refusal in the
extension's error shape.

### Events

The executor yields `TaskStatusUpdateEvent` and `TaskArtifactUpdateEvent`
and writes them whole; the server does not translate them:

- the Event extension's markup of its messages (`type`, `source`, `id`);
- its object under its URI in the event's metadata, written whole on every
  update that changes it: a2a-sdk merges event metadata into the task with
  `Struct.MergeFrom`, which replaces a key's value rather than merging into
  it. `AionTaskManager` keeps the current status message on a `WORKING`
  update with no message, so a details update does not blank the status
  line. A later turn reads the object from `context.current_task`;
- the masking of secret values in everything it reports, and any limits of
  its own;
- a live-only update marked with `mark_status_event_ephemeral`
  (`aion.core.a2a.metadata`);
- an error as `error_parts(uri, code, text, retryable)`
  (`aion.core.a2a.errors`): a text part for a person and a data part
  `{code, message, retryable}` with schema `{uri}#ExtensionError` for a
  program.

The request executor asks the executor for its next event once it has put
the previous one on the run's queue; a2a-sdk stores it afterwards, on its
own schedule. So an executor that waits for the server to take an event knows
the event is queued, not stored: a server that stops in between loses it. An
executor that announces an external action (a push) before taking it keeps
the action's outcome recoverable outside the task (the pushed branch), not
only in the announcement.

### Numbers

A payload and the executor's object travel as protobuf `Struct` values - a
data part of a message, `task.metadata` - and a `Struct` holds every number
as a double: an integer a client sends in a directive, or one the executor
wrote into its object, arrives as `3.0`. A payload model declares an integer
field as `int`, which pydantic (in its default, non-strict mode) fills from
`3.0` and refuses for `1.5`, and a fractional one as `float`. Numbers are
never sent as strings.

### The conversation of a turn

The request executor installs the history of every turn it runs on the
turn's runtime context (`AionRuntimeContext.conversation`), for the
framework executor and an extension's alike: `StoredConversations.for_turn`
over the task store, `ContextRuns` and `ContextArtifacts`. An executor - or
an agent's own code - reads it with `aion.core.agent.conversation`, whose
functions take the current turn's history; outside a turn they raise
`ConversationUnavailableError`. `conversation.history()` returns the history
object itself, so an executor can hand its bound methods on, as the
evolution wrapper hands `find_artifact` to its toolkit.

Every read is of the turn's context, for the holder the caller was admitted
as (`holder_of`): a private context's tasks are its user's, a shared
context's are all its participants'. Each read first checks that this holder
still holds the context (`ContextRuns.holds`), so a context being deleted,
or one another holder presents under the same id, reads as empty. This is
the holder, not the task owner. Every read is a query, not a cache, so any
server of the agent answers from what the store holds.

| Read | Answer |
| --- | --- |
| `current_task()`, `get_task(task_id)` | A task of the context as the store holds it, with its history and artifacts; None for any other task. |
| `tasks(unfinished=False)` | The context's tasks, oldest first; with `unfinished`, those without an outcome. |
| `generation()` | An opaque marker of this use of the context id: new when a deleted context's id is taken again. |
| `find_artifact(id)`, `artifact_versions(id)` | The newest version of an artifact in the context, or all of them, newest task first, as `ArtifactVersion` (`task_id`, `artifact`, `version`, `updated_at`). |
| `artifact_version_to_write(id)` | The number the turn's task writes an artifact under. |

### Artifact versions

The same artifact id in several tasks of a context is one artifact in
several versions, numbered from 0 in the artifact's `metadata["version"]` -
the key and the decimal-string value the ADK adapter writes too, so a client,
`GetContext` and a lookup read it alike for either kind of agent. An
executor that versions its artifacts calls
`conversation.stamp_artifact_version(event)` before it yields an artifact
event: on the first event of an artifact id it reads the context's versions
of that id (`artifact_version_to_write`) and takes the next number; chunks
and replacements of the artifact in the same turn, and the artifact in a
later turn of the task, keep the number the task already has. A caller that
does not hold the context sees no versions, so it numbers from 0.
`ContextArtifacts` has an in-memory reader and a PostgreSQL one
(`TasksRepository.find_artifact_versions`, which filters by `artifact_id`).

Two tasks of one context that run at the same time can compute the same
number; an extension that needs one run per context refuses a second one.

## Behavior evolution

`EvolutionExecutor` connects the evolution toolkit to the server and holds no
evolution logic. It imports the toolkit's module
(`aion.toolkits.behavior_evolution.plugin`) lazily: a server that does not
enable the extension never imports it, and one that enables it without the
toolkit does not start, saying "the behavior-evolution toolkit is not
installed". A missing module counts as the toolkit only when it is on the
toolkit's own path; a toolkit whose own import fails (a dependency it lacks)
is reported with that error.

Who may start an evolution is the agent's option (`EvolutionOptions`);
where its model calls go is the deployment's variable, read once when the
server starts:

| Setting | Where | Values | Default |
| --- | --- | --- | --- |
| `callers` | `aion.yaml` | `platform` - a call the platform dispatched, with an invocation token; `anonymous_session` - a client with an anonymous session token Aion issued | `[platform]` |
| `EVOLUTION_MODEL_PROVIDER` | environment | `aion` - the Aion model service on the caller's behalf; `custom` - an OpenAI-Responses-compatible endpoint; `local_session` - the operator's own Codex login on the host | `aion` |

A server the platform hosts accepts invocation tokens only, so
`anonymous_session` in `callers` admits nobody there and the same
`aion.yaml` serves a developer's machine and the platform. A developer sets
`EVOLUTION_MODEL_PROVIDER=local_session` (or `custom`) in their `.env`; a
deployment leaves it unset. A value that names no provider stops the server.

- **Created** when the toolkit is installed and the provider has what it
  needs - `aion`: `AION_CLIENT_ID` and `AION_CLIENT_SECRET`; `custom`:
  `EVOLUTION_MODEL_BASE_URL`, with `EVOLUTION_MODEL_API_KEY` when the
  endpoint takes a key - and the toolkit's own `availability()` passes (its
  settings, `codex`, `git`).
- **A new task is refused**, in this order, with the task closed `REJECTED`:
  a caller whose kind `callers` does not list - an application's own user
  among them - with `caller_not_allowed`, naming the kinds allowed; with
  `aion`, a caller the model service cannot be called for, the same way; a
  directive its model rejects with `invalid_payload`; what the toolkit's
  `preflight` refuses; and a context that already has an unfinished
  evolution task with `context_busy`, naming it. The caller comes first, so a
  caller the options do not allow learns nothing of the context.
- **A run**: the wrapper prepares the model access for the provider - the
  model service's base URL, a token minted for every call and the caller's
  attribution header; the custom endpoint, with its key read at every call
  when it has one; nothing for `local_session` - and hands the toolkit the
  context's generation (`conversation.generation()`), the deployment's
  secret values (`deployment_secret_values`), which it masks in what it
  reports, and the lookup of the context's `evolution-spec`. The toolkit
  yields the run's A2A events; the wrapper numbers the artifacts among them.
- **A cancel or a shutdown** reaches the toolkit's `cancel(task_id,
  within=)`: the run rescues its work and reports its closing in a `WORKING`
  update, and the server writes the final status.
- **A further message** never reaches the toolkit: an evolution never waits
  for input, the server refuses a message into its running task with
  `task_busy`, and a finished task takes none. One that reaches a task left
  waiting for input is a fault: `resume` raises, and the server logs it and
  fails the task with `internal_error`.
- The extension requires the daemon extension.

## Routing: before a task exists

`AionRequestHandler._setup_active_task` runs, in this order:

1. context admission - the caller may use the message's context;
2. `verify_declared_extensions` - every extension the request declares is
   registered, enabled, available and has its requirements declared;
3. the route - which executor the message belongs to
   (`AionRequestHandler._admit_route`);
4. preprocessors (file uploads);
5. a2a-sdk's own setup, which creates the `ActiveTask`.

The executor a message addresses is the first executable extension of the
pool it activates (`AionAgentRequestExecutor.route`), or the agent's
framework executor. The message continues a task when it names one, or when
the addressed executor has the caller's newest task in the context and that
task waits for input (`INPUT_REQUIRED`, `AUTH_REQUIRED`;
`AionRequestContextBuilder.interrupted_task`): an extension's request
continues a task of that extension, any other message a task of the
framework executor. A waiting task of another executor keeps waiting, and the
message starts a task of its own. A continued task is named on the message,
so a2a-sdk takes the message as an explicit continuation and nothing decides
again.

A message that continues a task keeps the task's route,
`task.metadata["aion:routedExtension"]`, and is checked against it
(`AionAgentRequestExecutor.admit_continuation`):

- a task routed to an extension this server does not serve - another image
  during a rollout - is refused with `extension_unavailable`; the task is
  left as it is and never reaches the framework executor;
- a running task (`SUBMITTED`, `WORKING`) whose executor refuses a message
  (`TaskExecutor.message_to_busy_task`, `"refuse"` for every extension
  unless it queues) is refused with `task_busy` naming the task itself:
  a2a-sdk would serve the message as the task's next turn, and on another
  server alongside the running one.

Any other message starts a task. Its route is kept on the request's
`ServerCallContext.state` (`EXTENSION_ROUTE_STATE_KEY`); `execute` reads it
and records the URI in `task.metadata["aion:routedExtension"]`, so the next
turn and a cancel find the executor again (`TaskExecutorPool.get`) on any server.

A refusal here is raised before a2a-sdk creates or changes a task. It is an
a2a-sdk `InvalidParamsError` whose `data` carries `code` and `retryable`,
and for `task_busy` the task's id under `taskId`; a2a-sdk sends `data` as
the `metadata` of a `google.rpc.ErrorInfo` detail, whose values are strings,
so `retryable` is `"true"` or `"false"` (`refusal_data`). A unary send
answers with the JSON-RPC error; a streaming send answers with the error as
the stream's only event.

| Refusal | `code` | `retryable` |
| --- | --- | --- |
| an extension the registry marks unavailable (`verify_declared_extensions`) | `extension_unavailable` | `"true"` |
| a message into a task whose extension this server does not serve | `extension_unavailable` | `"true"` |
| a message into a running task whose executor refuses one | `task_busy`, with the task under `taskId` | `"true"` |

The codes the server reserves and their fixed `retryable` live in
`aion.core.a2a.codes`. `invalid_payload` is the one code for any invalid
extension input: a client answers it the same way for every extension - fix
the request, do not retry - so an extension adds a code of its own only for
a refusal a client can act on differently.

## Turns

`AionAgentRequestExecutor` drives every turn the same way, whichever
executor runs it, and keeps a record of the turns running in this process.

### Outcome

A turn's events go to the pipeline, which stores files given as bytes,
delivers ephemeral updates without storing them, and moves the task to
`WORKING`. A turn ends where its executor ends it; what the server writes:

- a turn that raises `ExtensionRejection` is closed `REJECTED` with the
  refusal;
- a turn that raises an error carrying a `public_reason` - the agent's
  `TaskFailedError`, an SDK error naming what the deployment lacks - is
  closed `FAILED` saying so; any other error is a crash: logged with its
  traceback, the task closed `FAILED` with `CRASH_REASON`, which names the
  task id and points at the log;
- a file that could not be stored fails the task; a framework executor is
  asked to forget the event (`discard_undelivered`).

A task of an executable extension carries its outcome in the extension's
error shape, with the code: the refusal's, `internal_error` for a failure,
`server_shutdown` for a shutdown. Nothing is written over an outcome the
turn already reported. A turn that ends without a final status leaves its
task as it was, as a framework's does; an abandoned task is settled later
([Abandoned tasks](#abandoned-tasks)).

### Cancel

`CancelTask` always answers with a task that has an outcome, as in a2a-sdk.

- **The turn runs on this server.** a2a-sdk calls `cancel` under its lock on
  the task; the request executor asks the turn's executor to stop it
  (`TaskExecutor.cancel(config, within=CANCEL_GRACE_SECONDS)`). An executor
  that cannot stop a turn (`UnsupportedOperationError`, the default) is not
  waited for: `CANCELED` is written into the cancel's queue at once. One that
  can is given up to 15 s: what the turn yields while it stops reaches the
  task, and when the turn ends the run closes the task `CANCELED` through its
  own queue, so the stream open on it closes with it - unless the turn closed
  the task with an outcome of its own first, which `CancelTask` then answers
  with. Past the grace period `CANCELED` is written into the cancel's queue;
  a2a-sdk then cancels the turn. The wait is short because it holds the lock:
  a `SubscribeToTask` that arrives meanwhile is answered without the lock
  (`AionRequestHandler.on_subscribe_to_task`).
- **The turn runs on another server, or nowhere.** The task's executor is
  told (`within=None`); a2a-sdk writes `CANCELED` over the stored version and
  answers at once; `AionRequestHandler._write_cancel` sends its push
  notification. The server running the turn learns of it at its next durable
  write: the version conflict makes a2a-sdk cancel its producer, and the
  turn, torn down before its outcome, asks its executor to stop
  (`within=None`). Nothing more is written into the task. Until that write
  the turn goes on: the delay is up to the executor's next durable event.
- **No extension is activated.** A cancel runs with the caller's attribution
  and none of the request's extensions: a daemon named in `A2A-Extensions`
  has no payload on a cancel and must not fail it.

### Stopping

A turn is asked to stop at most once. `within` tells the executor how many
seconds from the call the server waits for the turn to end:
`CANCEL_GRACE_SECONDS` for a caller's cancel, the turns' window of the
shutdown budget at a shutdown, and None when it waits for none - a turn torn
down, a run without a consumer, a cancel of a task whose turn does not run
here.

- **Server shutdown.** `AION_SHUTDOWN_TIMEOUT` (seconds, 25 by default) is
  the agent's shutdown budget: `aion serve` gives every agent that long, all
  of them at the same time, before it kills them, and the agent divides it
  among its steps (`aion.server.shutdown.ShutdownBudget`). The agent's
  uvicorn server (`_AgentServer` in `aion.server.server`) stops listening and
  closes idle connections, then runs `AppFactory.stop_turns`: every turn
  running in the process is asked to stop, with the turns' window as
  `within`, all at once, and the turns whose executor can stop them are
  awaited together for that window (`AionAgentRequestExecutor.stop_turns`).
  A stopped turn that ends without an outcome is closed `FAILED` coded
  `server_shutdown` (retryable) through the run's queue, so the stream open
  on the task, a push and the store all get it. uvicorn then waits for the
  connections still open (`timeout_graceful_shutdown`) and cancels them, and
  the drain of the active tasks settles a turn that did not stop as `FAILED`
  with `aion:settledReason=server_shutdown` and pushes it; what such a turn
  did not save is lost with the process. Of the default 25 s the turns get
  15, uvicorn 5, the settlement 2 and the rest of the cleanup 3. A smaller
  budget shrinks the last three - uvicorn at most 20 % of it - and the turns
  get the rest, at least 1 s; below 10 s the agent logs a WARNING at startup.
  A larger one goes to the turns. A deployment sets the variable a few
  seconds below its platform's stop timeout (Kubernetes
  `terminationGracePeriodSeconds`, `docker stop -t`), so the server finishes
  its shutdown before the platform kills it.
- **A run without a consumer.** When the consumer of a run's events fails -
  a store write that is not a version conflict, the store unreachable -
  a2a-sdk closes the run's queue and drops whatever the run writes after it,
  without stopping the run. Before each event the request executor checks
  `EventQueue.is_closed()`; once the queue is closed it asks the executor to
  stop, logs it, and ends the turn without writing. The task keeps what
  a2a-sdk wrote for it: `FAILED`, or the active state, which a cancel ends.

### Abandoned tasks

A task in `SUBMITTED` or `WORKING` with no durable write for longer than
`AION_STALE_TASK_TIMEOUT` (seconds, 3600 by default, `0` off) is taken for
abandoned: its server died or hung. `aion.server.tasks.abandoned` settles it
`FAILED` with `aion:settledReason=task_abandoned`; a task routed to an
executable extension also gets, as its status message, the error a failed
turn closes with (`code` `task_abandoned`, `retryable` true, schema
`{uri}#ExtensionError`), so its client reads both alike. There is no lease
and no heartbeat, only the time of the task's last write, read on the
database's clock. The write is the one a2a-sdk's remote cancel makes - at
the version read, with its journal event, then a push notification - so a
task written in the meantime keeps its state, and a turn still alive on some
server meets the version conflict at its next write and is torn down.

Two places settle, on every server, never a task whose turn runs in their
own process and never one waiting for input:

- a message that names an abandoned task
  (`AionRequestHandler._settle_if_abandoned`); the message is then refused
  as one into a task that has an outcome;
- a background sweep over the agent's tasks every 60 s, or every half of a
  smaller timeout (`AionRequestHandler.sweep_abandoned_tasks`, started by the
  app's lifespan). A stale task whose turn runs in this process is logged as
  a WARNING and left: it is alive here, however silent. One server at a time
  sweeps an agent: with PostgreSQL a sweep takes an advisory lock
  (`pg_try_advisory_xact_lock`, keyed by the agent id) without waiting and
  holds it for the whole sweep, and a server that finds it held skips that
  sweep (DEBUG log) and tries again at the next interval.

An abandoned task keeps its context's unfinished task for an extension that
runs one task per context until it is settled. A turn that runs longer than
the timeout without yielding a durable event - a status, a message, an
artifact - is taken for abandoned by the other servers of the agent; an
executor writes a status now and then during long work.

## Testing

`tests/support/executors.py` holds `FakeFrameworkExecutor`, a framework
executor whose turns are the callables a test gives it, and
`request_executor`, the request executor over it and any extension
executors.

`tests/support/scripted_executor` holds an extension's executor that yields
what its script says, `ScriptedExecutor`, registered under its own URI with
a script event of its own. A turn's script is the `Script` the executor was
built with in a unit test, or the `ScriptPayload` a request carries
(`scripted_message` builds one). Its outputs are A2A events or their JSON
form (a status update, an artifact, an error); a script can also refuse
before it yields anything, crash after some outputs, look an artifact up in
its context, or wait for `cancel()` - writing on a tick meanwhile, and then
reporting the `within` it was given as `stopped_within` in its object and
ending without an outcome, ending `COMPLETED`, or not ending at all. It
numbers and looks up its artifacts with the turn's history
(`aion.core.agent.conversation`), as an extension's executor does; a turn it
runs without one, outside the request executor, neither numbers nor finds. A script with `report` has a turn say what the
executor was handed: how the turn began, its task, the object the task held
and every `cancel()` it was asked for - which is how a scenario, whose server
is another process, reads the record.

`fake_toolkit/` holds a stand-in for the evolution toolkit's module, scripted
the same way through the directive. With that directory on `sys.path` -
`PYTHONPATH` for a server process - the SDK's evolution wrapper finds it as
the installed toolkit; the real one, possibly private, is never installed in
the SDK's CI.

| Suite | Where | What |
| --- | --- | --- |
| unit | `tests/unit/server/agent/test_turns.py` | the turn for every executor: outcome, refusal, crash, resume, cancel and its grace period, a turn torn down, a run without a consumer, the route, a message into a running task by the executor's `message_to_busy_task`, the conversation every turn reads, shutdown |
| unit | `tests/unit/server/agent/test_request_executor.py`, `tests/unit/server/core/test_preprocessor_ordering.py` | the pool's routing in `execute` and `cancel`, the route of a new and a continued task |
| unit | `tests/unit/server/agent/extensions/` | registration, creating the executors and the options of `aion.yaml`; artifact versions and their lookup; the evolution wrapper with the stand-in toolkit and without it |
| unit | `tests/unit/server/tasks/test_conversation.py` | the conversation of a turn: its tasks, oldest first and unfinished, its generation, artifact versions, a caller that does not hold the context, a context being deleted, a read outside a turn |
| unit | `tests/unit/server/test_server.py`, `tests/unit/server/test_shutdown.py` | the order of a shutdown, `AION_SHUTDOWN_TIMEOUT` and its division among the steps |
| unit | `tests/unit/server/tasks/test_abandoned.py`, `tests/unit/server/core/test_request_handlers.py` | what is abandoned and what its settlement writes, a message into an abandoned task |
| integration | `tests/integration/server/tasks/test_extension_admission_postgres.py` | a refusal closes its task `REJECTED`; a message into a running extension task refused on every server; a message into an extension's task on a server without the extension refused; an abandoned task settled by a message, the server still running it stopping at its next write |
| integration | `tests/integration/server/tasks/test_context_artifacts_parity.py`, `test_context_runs_parity.py`, `test_abandoned_tasks_postgres.py` | artifact versions, who holds a context, its generation and its tasks, the settlement - on the in-memory store and on PostgreSQL |
| scenarios | `tests/scenarios/core/test_evolution.py` (`TAGS=evolution`) | evolution through a real `aion serve` with the stand-in toolkit: the standard path and the object in the task, numbered artifact versions, refusals as `REJECTED` tasks, a server without the toolkit that does not start, a cancel, the grace period, `context_busy` and `task_busy`, a directive beside the agent's waiting question, a directive into an evolution task left waiting failing it |
| scenarios | `tests/scenarios/persistence/test_evolution.py`, `tests/scenarios/distributed/test_evolution.py`, `tests/scenarios/distributed/test_abandoned_tasks.py` | the versions across a restart, a shutdown, a small shutdown budget; `context_busy`, `task_busy`, `extension_unavailable` and a remote cancel across servers; abandoned tasks |

The evolution toolkit is tested in its own repository; see
[Continuous integration](ci.md#executable-extensions-and-toolkits).
