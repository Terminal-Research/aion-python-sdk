# Repo guidelines

This repository is the Aion Python SDK monorepo. Every distributable package
lives under `libs/`; shared documentation lives under `docs/`, and the
repo-wide tooling lives in `Makefile` and `scripts/`.

- Whenever you add or modify a subproject, update this file with a brief
  description so agents can understand its purpose.

## Layering

Packages are layered; a package may only depend on layers below it.

```
aion-core                        (no internal deps)
  └── aion-api-client, aion-db
        └── aion-mcp
              └── aion-authoring-langgraph / aion-authoring-adk
        └── aion-server
              └── aion-server-langgraph / aion-server-adk
                    └── aion-sdk  (umbrella CLI, framework packages as extras)
```

Authoring packages are what agent authors import; they must never pull in
server or plugin machinery. Server packages implement `AgentPluginProtocol`
and are discovered by `aion-server` at runtime.

## Projects

### Foundation

- **aion-core** — foundation layer with no internal Aion dependencies:
  A2A protocol models, enums, request/response and artifact types, A2A
  extension payloads (`cards`, `distribution`, `messaging`, `event`,
  `traceability`), shared extension URI constants, `aion.yaml` configuration
  parsing and publication collectors (including dedicated secret fields),
  invocation abstractions (`card`, `message`, `thread`), the runtime context
  hierarchy (builder, registry, context extensions), settings
  (`BaseEnvSettings`, `ApiSettings`), the `DbManagerProtocol` interface,
  singleton metaclasses, `get_logger()`, and pydantic/text/url/path utilities.
  Owns the provider-neutral Distribution/Messaging context hierarchy, the
  reply contract, and the provider payload fixtures that verify it.
- **aion-api-client** — low-level Aion control-plane access: a websocket
  GraphQL client generated with `gql` + `ariadne-codegen` (`aion.api.gql`),
  an HTTP client and JWT manager (`aion.api.http`) that authenticates via
  `AION_CLIENT_ID`/`AION_CLIENT_SECRET` against `/auth/tokens` and exposes the
  deployment version those credentials are scoped to (`get_version_id()`, read
  from the token's `sub`/`sub_type` claims), typed
  control-plane addressing (`aion.api.control_plane`: `CapabilityReference`,
  `CapabilitySubject`, `PrincipalSelector`, path helpers), and the
  OpenAI-compatible `model_service_client` with request-scoped
  model-service principal header injection.
- **aion-db** — centralized DB management layer under the `aion.db.postgres`
  namespace: `DbManager`, `DbFactory`, task records/models, fenced task-claim
  records, repositories, Alembic migrations, custom fields/types, and utilities
  (`convert_pg_url`, `verify_connection`, `validate_permissions`). Structured so
  sibling namespaces (`aion.db.redis`, …) can be added later. Used by
  `aion-server` and server-side framework packages.
- **aion-mcp** — MCP integration utilities: an ASGI proxy for a local MCP
  server declared in `aion.yaml` (`proxy.py`) and authenticated remote Aion
  MCP endpoint builders (`endpoints.py`) for direct capability servers and the
  control-plane MCP server.

### Server

- **aion-server** — generic A2A server with plugin-based framework support,
  built on the Google `a2a-sdk` and Starlette/FastAPI. Contains the app
  factory, lifespan and route registry (`core/app`), middlewares, the
  platform websocket link (`core/platform`), the agent card/factory and
  execution adapters (`agent/`), the plugin registry and factory
  (`plugins/`), fenced task stores, expiring task ownership supervision, task
  manager, event deduplicator and push
  notification senders (`tasks/`), file storage and A2A file handling
  (`files/`), Aion auth manager and websocket connection services
  (`services/aion`), OpenTelemetry wiring, logging setup with stream and
  Logstash handlers, and the proxy server (`aion.proxy`) that fronts multiple
  agents. Graphs and HTTP apps are configured via `aion.yaml` and can be
  mounted dynamically. JSON-RPC streams use LF-delimited SSE events so their
  blank event boundary remains distinct from HTTP/1.1 CRLF transfer framing.
  The proxy preserves streaming response bodies and establishes fresh
  downstream transfer framing rather than buffering agent SSE output;
  contract tests cover published configuration schemas, including compact
  discovery documents that omit null field metadata.
  Push notifications authenticate against external callbacks using the
  credentials in `taskPushNotificationConfig.authentication` (the a2a-sdk
  base sender ignores them); delivery timeouts come from
  `PUSH_NOTIFICATION_TIMEOUT_SECONDS` (httpx's 5s default is too short for a
  receiver that works before answering). The Logstash endpoint is derived
  from `LOGSTASH_HOST` (bare host or full URL) with `LOGSTASH_PORT` as a
  fallback; platform endpoints require an Aion bearer token and are skipped
  rather than erroring while no token is available. Every outbound stream is
  closed with a full `Task`; tasks left active by a stopped process are settled
  as `FAILED` with `aion:settledReason` naming the cause — `server_shutdown`
  when the shutdown itself cancelled the execution, `server_restart` when the
  next start found them still active after a hard kill. DB management is
  delegated to `aion-db`.
- **aion-server-langgraph** — server-side LangGraph integration under
  `aion.langgraph.server`. Implements `AgentPluginProtocol`/`AgentAdapter`,
  adapts inbound A2A requests into `graph.astream()` invocations and maps
  graph output back into A2A messages, tasks and streaming events. Includes
  execution, checkpointing, state handling, converters, and A2A extension
  support. Selected with `framework: "langgraph"` in `aion.yaml`; the agent
  path must resolve to a `StateGraph`, a compiled `Pregel`, or a callable
  returning one.
- **aion-server-adk** — server-side Google ADK integration under
  `aion.adk.server`: `ADKPlugin`/`ADKAdapter`, `ADKExecutor` and
  `ADKStreamExecutor`, session services (memory and PostgreSQL) via
  `SessionServiceFactory`, artifact services (memory and A2A-backed) via
  `ArtifactServiceFactory`, `StateConverter` mapping ADK session state to
  `ExecutionSnapshot`, and bidirectional A2A ↔ ADK transformers.

### Authoring

- **aion-authoring-langgraph** — LangGraph authoring toolkit under
  `aion.langgraph.authoring`: `aion_chat_model` and other model helpers that
  route LangChain/LangGraph calls through Aion's OpenAI-compatible model
  proxy and resolve principal headers at request time, state and streaming
  helpers, event-routing utilities, invocation helpers, MCP tool loading, and
  provider-neutral immediate context and direct-reply routing. Includes
  tested Slack distribution examples that resolve provider tools from the
  incoming runtime capability.
- **aion-authoring-adk** — Google ADK authoring toolkit under
  `aion.adk.authoring`: MCP toolset bindings, request-scoped Aion model
  helpers, invocation helpers and transformers, without pulling in server
  plugin machinery.

### Entry points

- **aion-sdk** — umbrella distribution and CLI exposing the `aion` entry
  point (`aion serve`, `aion chat`, `aion logs`). `serve` launches all agents
  declared in `aion.yaml` plus the proxy server with automatic port
  assignment; `chat` (including headless `aion chat run`) delegates to the
  standalone chat UI bundled at `src/aion/cli/bin/cli.mjs` and sets the
  Python credential-helper environment for chat auth. Framework support is
  opt-in through extras: `langgraph-authoring`, `langgraph-server`,
  `adk-authoring`, `adk-server`, `all`.
- **aion-chat-ui** — standalone React/Ink terminal chat UI in TypeScript.
  Published to npm as `@terminal-research/aion`, which installs the `aio`
  executable with an `aion-chat` alias, and staged into `aion-sdk` via
  `npm run stage:python`. Provides interactive chat, headless one-shot `run`,
  slash-command request/response mode controls, update prompts with GitHub
  release-note links, environment-scoped agent source discovery, local
  session/settings persistence, and WorkOS CLI/device login with npm keyring
  storage or the Python credential helper supplied by `aion-sdk`. See
  `libs/aion-chat-ui/AGENTS.md` for session-log inspection and package-local
  conventions.

## Repo tooling

- `make help` lists all targets. `make tests` runs the unit suite;
  `make tests-integration` runs the integration suite and
  `make tests-all` runs both. A test marked `integration` needs a real
  PostgreSQL or real child processes and waits for real timeouts, so it is not
  what you run between two edits - run it before you commit. The database is
  handled for you: both integration targets start a disposable container,
  run the suite, and stop the container again, carrying the suite's exit
  status across the teardown. `PG_TEST_KEEP=1` leaves it up between runs while
  you debug one, and `make pg-test-up` / `make pg-test-down` drive it by hand.
  Setting `POSTGRES_TEST_URL` yourself - in CI, or at a PostgreSQL of your own
  - takes over completely and nothing touches Docker. That variable is named
  apart from the ordinary connection setting on purpose: these tests migrate
  and truncate what they are pointed at. Without it `scripts/tests.py
  --integration` refuses to run rather than reporting a pass for a suite that
  skipped itself. `scripts/tests.py`
  discovers every `libs/aion-*` package with a `tests/` directory and runs its
  suite (libs without tests are skipped). Prefer it over calling `pytest` by
  hand: the whole repo takes under three seconds, because the libs run
  concurrently and each is addressed through its own `.venv/bin/python` rather
  than through `poetry run`, which starts a second interpreter to work out that
  same path. Poetry remains the fallback for a lib whose venv is missing.
  Target specific packages with `python scripts/tests.py aion-core aion-db`,
  `--jobs 1` to run them one at a time, and `--fail-fast` to skip whatever has
  not started yet (libs already running are left to finish - their output would
  otherwise be lost). Anything after a bare `--` goes to pytest untouched, so
  narrowing a run needs no detour around the script:
  `make tests ARGS="aion-sdk -- tests/handlers -q"` or
  `python scripts/tests.py -- -k websocket`. A lib that matches nothing is
  reported as `no tests` rather than as a pass or a failure. Almost all of a suite's wall time is importing, not
  testing, so a lib with twelve tests costs about as much as one with a
  thousand.
- Internal packages depend on each other by git reference, not by version, so
  a shared-dependency bump does not lock against `main`. For cross-package
  work use `make deps-use-local` (switch to local paths, lock, sync, verify)
  and `make deps-use-remote` to return. `make deps-set-branch BRANCH=...`
  repoints git references at a feature branch. `poetry.lock` files are not
  committed.
- `.github/workflows/publish-aion.yml` publishes the `aion-chat-ui` npm
  package on GitHub release.

## Documentation

User-facing docs live in `docs/`: `environment-variables.md`,
`aion-yaml-config.md`, `multiple-agents.md`, `app-registry.md`,
`http_endpoints.md`, `a2a_extensions/`, and `development/` (environment and
dependency workflows). Keep them in sync with behavioural changes.

## Additional guidelines

1. Always use idiomatic Python and best practices. Use `snake_case` for Python
   module names, variables, attributes, dataclass fields, function parameters,
   and keyword arguments; reserve camelCase only for explicit wire-format
   aliases or protocol field names.
2. Document all code with detailed Python docstrings in Google's style,
   especially at the class and method level; avoid overly terse summaries.
3. Create thorough unit tests for all code using pytest.
4. For docstring sections that define a group of injected or projected fields,
   use a hanging-indent aligned definition list: left-align the field names in
   a fixed-width first column and align descriptions after an em dash.
5. Respect the layering above: authoring packages stay free of server
   dependencies, and model helpers belong in the authoring package for their
   framework rather than in the server plugin.
