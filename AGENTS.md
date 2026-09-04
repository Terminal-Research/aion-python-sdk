# Repo guidelines

This repository is the Aion Python SDK: **one Python project**, `aionto-sdk`,
whose sources live under `src/aion/` as subpackages of one namespace. It builds
one wheel and one sdist; the extras install third-party libraries, never Aion
code, so every `aion.*` subpackage is present in every installation and what an
extra decides is whether its dependencies are importable.

The tests for all of it live in `tests/`, mirroring `src/aion/`. Shared
documentation lives in `docs/`, repo-wide tooling in `Makefile` and `scripts/`.
`libs/` holds one thing only: `aion-chat-ui`, an npm package with its own
toolchain.

- Whenever you add or modify a subpackage, update this file with a brief
  description so agents can understand its purpose.

## Layering

The subpackages are layered; a subpackage may import the layers below it and
nothing above. The rule is not advisory — it is a contract in
`[tool.importlinter]` of the root `pyproject.toml`, checked by
`make lint-imports`:

```
aion.cli
  └── aion.langgraph.server | aion.adk.server
        └── aion.proxy
              └── aion.server
                    └── aion.db
                          └── aion.langgraph.authoring | aion.adk.authoring
                                └── aion.mcp
                                      └── aion.api
                                            └── aion.core
```

Names joined by `|` share a rank and, by the same rule, may not import each
other: that is what keeps the LangGraph and ADK sides from growing a dependency
on one another. A second, `forbidden` contract states separately that
`aion.langgraph.authoring` and `aion.adk.authoring` never import `aion.server`,
`aion.proxy`, `aion.db` or `aion.cli` — this is the property the published
extras are sold on, and it should fail by name when broken.

The ten former `libs/*` packages kept the layers apart by accident: a package's
venv simply had no code from the layers above it. One project, one venv, so the
rule has to be written down and checked.

Authoring subpackages are what agent authors import; they must never pull in
server or plugin machinery. Server subpackages implement `AgentPluginProtocol`
and are discovered by `aion.server` at runtime.

## Subpackages

### Foundation

- **`aion.core`** — foundation layer with no internal Aion dependencies:
  A2A protocol models, enums, request/response and artifact types, A2A
  extension payloads (`cards`, `distribution`, `messaging`, `event`,
  `traceability`), shared extension URI constants, `aion.yaml` configuration
  parsing and publication collectors (including dedicated secret fields),
  invocation abstractions (`card`, `message`, `thread`), the runtime context
  hierarchy (builder, registry, context extensions), settings
  (`BaseEnvSettings`, `ApiSettings`), the `DbManagerProtocol` interface,
  singleton metaclasses, `get_logger()`, the `MissingOptionalDependency`
  helper that names the extra a missing library belongs to
  (`utils/optional_deps.py`), and pydantic/text/url/path utilities.
  Owns the provider-neutral Distribution/Messaging context hierarchy, the
  reply contract, and the provider payload fixtures that verify it.
- **`aion.api`** — low-level Aion control-plane access: a websocket
  GraphQL client generated with `gql` + `ariadne-codegen` (`aion.api.gql`),
  an HTTP client and JWT manager (`aion.api.http`) that authenticates via
  `AION_CLIENT_ID`/`AION_CLIENT_SECRET` against `/auth/tokens` and exposes the
  deployment version those credentials are scoped to (`get_version_id()`, read
  from the token's `sub`/`sub_type` claims), typed control-plane addressing
  (`aion.api.control_plane`: `CapabilityReference`, `CapabilitySubject`,
  `PrincipalSelector`, path helpers), and the OpenAI-compatible
  `model_service_client` with request-scoped model-service principal header
  injection. The generated client is committed; regenerate it from
  `graphql/schema.graphql` and `graphql/queries.graphql`
  (`[tool.ariadne-codegen]` in the root manifest).
- **`aion.db`** — centralized DB management layer under the `aion.db.postgres`
  namespace: `DbManager`, `DbFactory`, task records/models, fenced task-claim
  records, durable caller ownership for task contexts, repositories, Alembic
  migrations, custom fields/types, and utilities (`convert_pg_url`,
  `verify_connection`, `validate_permissions`). Structured so sibling
  namespaces (`aion.db.redis`, …) can be added later. Its third-party
  dependencies come with the `server` extra.
- **`aion.mcp`** — MCP integration utilities: an ASGI proxy for a local MCP
  server declared in `aion.yaml` (`proxy.py`) and authenticated remote Aion
  MCP endpoint builders (`endpoints.py`) for direct capability servers and the
  control-plane MCP server. `endpoints.py` works in a base install, so
  `import aion.mcp` must not reach the proxy: `load_proxy()` imports
  `proxy.py`, and with it the ASGI proxy libraries from the `server` extra,
  only when it is called.

### Server

- **`aion.server`** — generic A2A server with plugin-based framework support,
  built on the Google `a2a-sdk` and Starlette/FastAPI. Contains the app
  factory, lifespan and route registry (`core/app`), middlewares, the
  platform websocket link (`core/platform`), the agent card/factory and
  execution adapters (`agent/`), the plugin registry and factory
  (`plugins/`), fenced task stores, expiring task ownership supervision, task
  manager, event deduplicator and push notification senders (`tasks/`), file
  storage and A2A file handling (`files/`), Aion auth manager and websocket
  connection services (`services/aion`), OpenTelemetry wiring, and logging
  setup with stream and Logstash handlers. Graphs and HTTP apps are configured
  via `aion.yaml` and can be mounted dynamically. JSON-RPC streams use
  LF-delimited SSE events so their blank event boundary remains distinct from
  HTTP/1.1 CRLF transfer framing. Contract tests cover published configuration
  schemas, including compact discovery documents that omit null field metadata.
  Aion context-directory extensions resolve history through the same effective
  caller scope used when tasks are saved; anonymous callers receive empty
  context projections rather than access to shared history.
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
  delegated to `aion.db`. PostgreSQL task claims record the deployment-provided
  `HOST_NAME` as their optional diagnostic owner instance identity.
  Plugin discovery skips a framework whose extra is absent and keeps the
  reason, so an agent that cannot be built names the extra to install.
- **`aion.proxy`** — the proxy server that fronts multiple agents behind one
  endpoint. It sits above `aion.server` rather than beside it: it reads the
  server's settings and its port reservations. Streaming response bodies are
  preserved and fresh downstream transfer framing is established rather than
  buffering agent SSE output.
- **`aion.langgraph.server`** — server-side LangGraph integration. Implements
  `AgentPluginProtocol`/`AgentAdapter`, adapts inbound A2A requests into
  `graph.astream()` invocations and maps graph output back into A2A messages,
  tasks and streaming events. Includes execution, checkpointing, state
  handling, converters, and A2A extension support. The agent path must resolve
  to a `StateGraph`, a compiled `Pregel`, or a callable returning one.
- **`aion.adk.server`** — server-side Google ADK integration:
  `ADKPlugin`/`ADKAdapter`, `ADKExecutor` and `ADKStreamExecutor`, session
  services (memory and PostgreSQL) via `SessionServiceFactory`, artifact
  services (memory and A2A-backed) via `ArtifactServiceFactory`,
  `StateConverter` mapping ADK session state to `ExecutionSnapshot`, and
  bidirectional A2A ↔ ADK transformers.

### Authoring

- **`aion.langgraph.authoring`** — LangGraph authoring toolkit:
  `aion_chat_model` and other model helpers that route LangChain/LangGraph
  calls through Aion's OpenAI-compatible model proxy and resolve principal
  headers at request time, state and streaming helpers, event-routing
  utilities, invocation helpers, MCP tool loading, and provider-neutral
  immediate context and direct-reply routing. Tested Slack distribution
  examples that resolve provider tools from the incoming runtime capability
  live in `examples/langgraph/`. The `langchain` dependency belongs to the
  `langgraph-authoring` extra because `aion_chat_model()` imports LangChain's
  model factory directly; agent packages must not declare it themselves.
- **`aion.adk.authoring`** — Google ADK authoring toolkit: MCP toolset
  bindings, request-scoped Aion model helpers, invocation helpers and
  transformers, without pulling in server plugin machinery. The `litellm`
  dependency belongs to the `adk-authoring` extra, since `aion_lite_llm()`
  builds on ADK's `LiteLlm`; agent packages must not declare it themselves.

### Entry point

- **`aion.cli`** — the `aion` console script (`aion serve`, `aion chat`,
  `aion logs`). `serve` launches all agents declared in `aion.yaml` plus the
  proxy server with automatic port assignment, and asks for the `server` extra
  by name when it is not installed; `chat` (including headless
  `aion chat run`) delegates to the standalone chat UI bundled at
  `src/aion/cli/bin/cli.mjs` and sets the Python credential-helper environment
  for chat auth. The CLI module must stay importable in a base install — no
  import of `aion.server` at module level.

### Not a Python subpackage

- **`libs/aion-chat-ui`** — standalone React/Ink terminal chat UI in
  TypeScript. Published to npm as `@terminal-research/aion`, which installs the
  `aio` executable with an `aion-chat` alias, and staged into `src/aion/cli/bin`
  via `npm run stage:python`. Provides interactive chat, headless one-shot
  `run`, slash-command request/response mode controls, update prompts with
  GitHub release-note links, environment-scoped agent source discovery, local
  session/settings persistence, streaming-aware Marked rendering for agent
  output, immutable transcript offloading to terminal scrollback, TTY-aware
  terminal clearing for `/clear`, a brand-themed composer prompt with native
  cursor-aware multiline editing, and WorkOS CLI/device login with npm keyring
  storage or the Python credential helper supplied by the SDK. Its GraphQL
  operation types are generated from the restricted chat schema copied from
  `aion-api`; rebuild and run `stage:python` after contract changes. See
  `libs/aion-chat-ui/AGENTS.md` for session-log inspection and package-local
  conventions.

## Repo tooling

- One project, one environment: `poetry install -E all --with dev` at the root.
  Every `aion.*` import then resolves to the working tree, and there is nothing
  to switch between local and remote. `poetry.lock` is not committed.
- `make help` lists all targets. `make tests` runs the unit suite;
  `make tests-integration` runs the integration suite and
  `make tests-all` runs both. All three are plain `pytest` over `tests/` with
  the `integration` marker selecting: `pytest` is not to be reached around, and
  anything after `ARGS=` goes to it untouched
  (`make tests ARGS="-k websocket"`, `make tests ARGS="tests/server -q"`).
  A test marked `integration` needs a real PostgreSQL or real child processes
  and waits for real timeouts, so it is not what you run between two edits —
  run it before you commit. The database is handled for you: both integration
  targets start a disposable container, run the suite, and stop the container
  again, carrying the suite's exit status across the teardown. `PG_TEST_KEEP=1`
  leaves it up between runs while you debug one, and `make pg-test-up` /
  `make pg-test-down` drive it by hand. Setting `POSTGRES_TEST_URL` yourself —
  in CI, or at a PostgreSQL of your own — takes over completely and nothing
  touches Docker. That variable is named apart from the ordinary connection
  setting on purpose: these tests migrate and truncate what they are pointed at.
- `make lint-imports` checks the layer contract described above. Run it after
  moving code between subpackages; a new import that crosses a layer fails it.
- `scripts/packaging/check.py` (`make dist-check`) reads the built wheel and
  sdist against the packaging contract; `scripts/packaging/smoke.py`
  (`make dist-smoke`) installs them into six clean virtual environments and
  uses each one. Neither runs through `poetry run`: the point is an environment
  that inherits nothing from this project's. `make dist-build` empties `dist/`
  and builds. See `docs/publishing-pypi.md`.
- `.github/workflows/python-ci.yml` runs the unit suite on 3.12 and 3.13, the
  layer contract, and build + check on every pull request, plus an integration
  job against a `postgres:16` service container.
  `.github/workflows/publish-python.yml` builds, checks, smokes and publishes
  `aionto-sdk` to PyPI on a `py-v*` release, through trusted publishing and the
  `pypi` environment. `.github/workflows/publish-aion.yml` publishes the
  `aion-chat-ui` npm package on any other release; the two are kept apart by
  the `py-v` tag prefix.

## Documentation

User-facing docs live in `docs/`: `environment-variables.md`,
`aion-yaml-config.md`, `multiple-agents.md`, `app-registry.md`,
`http_endpoints.md`, `a2a_extensions/`, `modules/` (one page per subpackage),
and `development/`. `docs/publishing-pypi.md` is for maintainers. The root
`README.md` is the PyPI page — keep it short, and keep every link in it
absolute, since relative links do not resolve on PyPI. Keep all of them in sync
with behavioural changes.

## Additional guidelines

1. Always use idiomatic Python and best practices. Use `snake_case` for Python
   module names, variables, attributes, dataclass fields, function parameters,
   and keyword arguments; reserve camelCase only for explicit wire-format
   aliases or protocol field names.
2. Document all code with detailed Python docstrings in Google's style,
   especially at the class and method level; avoid overly terse summaries.
3. Create thorough unit tests for all code using pytest. A test module goes
   under `tests/` at the path mirroring its subpackage, and every directory
   under `tests/` carries an `__init__.py`: one rootdir means module basenames
   collide otherwise.
4. For docstring sections that define a group of injected or projected fields,
   use a hanging-indent aligned definition list: left-align the field names in
   a fixed-width first column and align descriptions after an em dash.
5. Respect the layering above: authoring subpackages stay free of server
   dependencies, and model helpers belong in the authoring subpackage for their
   framework rather than in the server plugin.
6. A new third-party dependency goes into the extra that owns it, and into the
   composite extras that include it — `langgraph-server`, `adk-server` and
   `all` are written out in full, and `make dist-check` is what catches the
   copy you forgot.
