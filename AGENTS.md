# Aion Python SDK

This repository builds one Python distribution, `aionto-sdk`, from `src/aion/`.
Every `aion.*` subpackage ships in the wheel and sdist. Extras install optional
third-party dependencies; they do not select Aion source packages. The
standalone npm package `libs/aion-chat-ui` has its own tooling and local
`AGENTS.md`.

Use this file for decisions that apply across the repository. Read the
relevant package README or maintainer guide when a task needs implementation
details. `pyproject.toml`, `Makefile`, and the tests define the current executable
contracts; reconcile this file with them when behavior changes.

## Working rules

- Keep Python docstrings, code comments, and maintainer documentation in
  English. Describe the current code and why it works as it does, without
  historical comparisons or obsolete behavior.
- Use idiomatic Python and `snake_case` for modules, variables, attributes,
  dataclass fields, parameters, and keyword arguments. Use camelCase only
  where an external wire format requires it.
- Document public APIs with Google-style docstrings. Explain non-obvious
  internal behavior where that helps maintainers; avoid boilerplate docstrings
  that merely repeat a name or signature. In sections that define injected or
  projected fields, align names and descriptions in a hanging-indent
  definition list separated by an em dash.
- Add or update tests for changed behavior at the appropriate boundary. Do not
  add tests that only restate the implementation or add a test requirement to
  a documentation-only change.
- Every SDK-owned exception derives from
  `aion.core.exceptions.AionError`. Put public exceptions in that module and
  local internal exceptions beside the code that raises them. Preserve
  `ImportError` or `RuntimeError` compatibility through multiple inheritance
  when applicable; `A2AError` subclasses remain in the a2a-sdk hierarchy.
- No general formatter or type checker is configured for this Python project.
  Follow surrounding style and do not introduce unrelated tooling changes.
- Keep unrelated user changes intact. Commit messages must read like ordinary
  developer messages, without AI attribution, `Co-Authored-By`, or generated-by
  markers. If a commit is blocked, preserve the staged state and give the exact
  manual commands.

## Architecture boundaries

The layer order is enforced by `[tool.importlinter]` in `pyproject.toml`.
A layer may import only layers below it. Names joined by `|` share a rank and
may not import one another.

```text
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

- Keep authoring subpackages free of server, proxy, database, and CLI
  machinery. They provide framework-facing tools; the server subpackages
  implement `AgentPluginProtocol` and own framework adaptation.
- Keep model helpers in the framework's authoring subpackage. Aion model
  service calls use request-scoped principal and usage attribution rather
  than a separate direct model client in a toolkit.
- Keep `aion.mcp` importable in a base install: import its ASGI proxy only
  through `load_proxy()`. Keep the `aion.cli` module importable without the
  server extra; do not import `aion.server` at module load time.
- Treat the production proxy HTTP surface and A2A streaming behavior as
  public contracts. Preserve streaming responses and valid transfer framing;
  verify compatibility with scenario tests when changing them.
- Keep extension identity, activation, availability, advertisement, transport
  bindings, and task ownership distinct. A method extension is not merely a
  message extension; a registered method is not automatically advertised.
  Generated Agent Cards use `get_advertised()`. An
  `ExtensionTaskHandler` owns execution only when it claims that extension's
  URI. Read `docs/development/extension-exposure.md` before changing
  extension exposure or routing.
- Preserve the external streaming contract: close every outbound stream with
  a full `Task`, including completed and failed executions and the
  initial-task resume path. Cancellation is a `Message`. Keep the shared
  `aion.server.a2a.outbox` behavior consistent across framework adapters.
  Use relevant unit and scenario tests when changing these paths.

## Dependencies and generated files

- Put dependencies required by the base install in
  `[project].dependencies`. Put optional dependencies in the owning extra;
  mirror them in the explicitly listed `langgraph-server` and `adk-server`
  composite extras where applicable. Shared dependencies belong to their
  owning package, not duplicate consumer declarations.
- Keep code compatible with Python 3.12 and the declared minimum dependency
  versions. `make tests-floors` checks those floors in an environment of its
  own, `.venv-floors` (it needs `uv`, and fetches Python 3.12 if it has to),
  and leaves the development environment alone. It fails on a floor that is
  neither installed nor recorded in `RAISED_BY_SIBLING` in
  `scripts/packaging/floors.py`; a floor raised on purpose goes there with
  the sibling that raises it.
- Do not commit `poetry.lock`. For the project environment, install with
  `poetry install -E langgraph-server -E adk-server --with dev`. The
  `installer.re-resolve = true` setting in `poetry.toml` is required; use
  `make check-env` after installation or dependency changes.
- Treat `src/aion/api/gql/generated/` as generated from
  `graphql/schema.graphql` and `graphql/queries.graphql`. Change the source
  operations or schema and regenerate with `poetry run ariadne-codegen client`
  instead of hand-editing generated client code.
- Treat `src/aion/cli/bin/cli.mjs` as a bundled output of
  `libs/aion-chat-ui`. Edit its TypeScript source and rebuild the bundle.
  Verify the staging destination when refreshing the Python bundle.

## Tests and validation

Run tests through the repository's Make targets so each suite gets its own
setup and teardown. `make help` lists targets; `tests/README.md` explains
selection. The suite directory determines its pytest marker, so do not add
`unit`, `integration`, or `scenario` markers by hand. Python test-package
directories need an `__init__.py` to avoid module-name collisions; data-only
fixture and configuration directories do not.

- Unit behavior: while developing, run the tests of the affected module, for
  example `make tests-unit TEST_PATHS="tests/unit/server" ARGS="-k websocket -x"`.
  Before finishing a task, run the whole unit suite with `make tests-unit`.
  It runs on four pytest-xdist workers; `UNIT_WORKERS=0` runs it in one
  process for `--pdb` or `-s`. `tests/unit` mirrors `src/aion`; shared unit
  builders live in `tests/unit/support`.
- Real PostgreSQL, real process trees, or elapsed lease/timeout behavior:
  `make tests-integration`, optionally with `TEST_PATHS=` and `ARGS=`.
  Run the relevant integration suite before committing changes to these
  boundaries. The target starts and removes a disposable PostgreSQL
  container unless `POSTGRES_TEST_URL` is supplied.
- Public A2A, proxy, agent-framework, or wire behavior: run the relevant
  `make tests-scenarios` selection. It starts real `aion serve` processes.
  `TAGS=` and `FRAMEWORK=` narrow it. Run database-backed groups with
  `make tests-scenarios-persistence` or `make tests-scenarios-distributed`.
  Read `tests/scenarios/README.md` for selection and scope.
- New or changed cross-package imports: `make lint-imports`.
- Extras, packaging, or installation behavior: `make dist-build` followed by
  `make dist-check`; use `make dist-smoke` when the clean-install contract
  changes. `make tests-scenarios-dist` runs selected scenarios against the
  built wheel. Check its marker selection when adding scenario groups.
- Changes to scenario inventory: `make scenarios-matrix` regenerates
  `tests/scenarios/SCENARIOS.md`; commit the generated matrix with the tests.
- Complete source-checkout validation: `make tests-full` runs unit,
  integration, ordinary scenarios, persistence, and distributed scenarios in
  sequence. It clears narrowing selectors and does not build or test the
  wheel. Run it deliberately, not as a routine end-of-task check: the
  full source-checkout gate runs on every pull request in CI. Require
  its `CI result` check in the `main` ruleset to block a failing merge; see
  `docs/development/ci.md`.
- Release preparation: `make release-check` runs the full non-publishing
  gate. `make release` publishes; follow `RELEASE.md` for that workflow.

`POSTGRES_TEST_URL` gives the test targets ownership of the specified
database: integration and database-backed scenario tests migrate and truncate
it. Point it only at a disposable test database. `PG_TEST_KEEP=1` retains the
local test container while debugging.

Run checks relevant to the change, fix failures caused by it, and report what
was run. There is no need to run every suite for a documentation-only edit.

## Repository map

- `aion.core`: protocol models, extension registry, configuration and runtime
  abstractions, settings, logging types, and the public exception hierarchy.
- `aion.api`: GraphQL and HTTP control-plane clients, token management,
  capability addressing, Files and model-service clients.
- `aion.mcp`: base-install MCP endpoint builders and a lazily loaded ASGI
  proxy.
- `aion.langgraph.authoring` and `aion.adk.authoring`: framework-facing
  authoring helpers, model routing, tools, and invocation support.
- `aion.db`: PostgreSQL manager, repositories, migrations, task claims, and
  durable ownership.
- `aion.server`: generic A2A app, plugin discovery, task and file lifecycle,
  platform link, extensions, push notifications, and observability.
- `aion.proxy`: public multi-agent proxy and streaming response handling.
- `aion.langgraph.server` and `aion.adk.server`: framework plugins and
  adapters for execution, state, streams, and A2A conversion.
- `aion.cli`: the `aion` command, including `serve`, `chat`, and `logs`.
- `libs/aion-chat-ui`: standalone React/Ink npm CLI; follow its local
  `AGENTS.md` for package-specific work.

Update this map only when a subpackage is added, removed, changes
responsibility, or moves in the layer order. Put implementation details in the
relevant package README or maintainer guide.

## Documentation ownership

User-facing docs live in the `aion-docs-mintlify` repository at
<https://docs.aion.to>. Link to the page that owns a topic rather than
duplicating it here; do not create Markdown files that only forward to a URL.
`docs/development/` holds repository-specific maintainer guides, and
`RELEASE.md` holds the release procedure. The root `README.md` is the PyPI
page: keep it short and use absolute links. Existing subpackage READMEs
describe their local code; update the document that owns behavior you change.
Test-suite guidance lives beside the suite, with the generated scenario matrix
maintained through `make scenarios-matrix`.
