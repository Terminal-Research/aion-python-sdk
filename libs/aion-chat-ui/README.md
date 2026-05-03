# aion-chat-ui

Standalone terminal chat UI for Aion agents. This subproject is published to npm as `@terminal-research/aion` and installs the `aio` executable with an `aion-chat` alias.

## Install

`aio` is the standalone Node entrypoint. `aion-chat` is available as an alias.

```bash
npm install -g @terminal-research/aion
aio --url https://agent.example.com
aio --agent-id my-agent --url http://localhost:8000
aion-chat --agent-id my-agent --url http://localhost:8000
```

## Commands

### Chat

```bash
aio --url http://localhost:8000
aio --agent-id my-agent --url http://localhost:8000
```

`--url` is an A2A endpoint URL used to discover and connect to agents. The selected Aion environment is separate from `--url`; it controls which Aion account and registry services are used for login and hosted agent discovery.

CLI endpoint auth values such as `--token` and `--header` are sent only to the explicit `--url` source. They are not sent to default localhost discovery or Aion registry-discovered agents.

### Headless Run

```bash
aio run --agent @team-agent "Summarize the latest status"
cat prompt.txt | aio run --agent @team-agent -
aio run --url http://localhost:8000 --agent-id demo-agent "Hello"
aio run --agent @team-agent --response-mode a2a "Hello"
```

`aio run` sends one message without opening the terminal UI. It uses the currently selected Aion environment for registry discovery and account-backed access, the same as interactive chat. Use `--agent` to select a discovered agent by handle, display id, identity id, or agent key. Use `--agent-id` with `--url` when you need proxy-aware routing for an explicit A2A endpoint.

By default, headless mode writes the rendered agent response to stdout and progress or diagnostic notices to stderr. `--response-mode a2a` writes raw A2A JSON instead: one JSON object for `send-message`, or JSONL events for `streaming-message`. `--request-mode streaming-message` falls back to `send-message` when the selected agent does not advertise streaming support.

The Python package forwards `aion chat run ...` to the same implementation. Headless runs skip the interactive npm update prompt.

### Login

```bash
aio login
aion-chat login
```

`aio login` signs in to the currently selected Aion environment, defaulting to production when no environment has been selected. The login flow opens the Aion sign-in page in the default browser when possible and shows the user code in the terminal. If your account needs additional setup, the CLI opens the appropriate Aion web app page after sign-in.

Inside the composer, `/login` is visible in the slash command picker and runs the same login flow.

### Updates

When an interactive chat session starts, `aio` checks npm for the latest published version. If a newer version is available, it asks whether to install it globally, install it in the current project, or continue without updating. Choosing an install option runs the npm command and exits; start `aio` again after the install completes.

### Agent Sources and Sessions

Agent sources are discovered per selected Aion environment. Every environment includes a default local source at `http://localhost:8000`; this default is silent when no local server is running. When you are logged in, the selected Aion environment can also provide registry-backed agents for that account. Passing `--url` adds an explicit source for that run. Explicit URLs are resolved as a manifest first and then as a direct agent card.

Inside the composer, `/sources` is visible in the slash command picker and lists configured sources, their type, URL, description, and current status.

The chat UI stores source and agent indexes in `chat2.json`. Context session previews are stored separately under the Aion config directory:

```text
~/.config/aion/sessions/<environment>/<agent-key>/<context-id>.json
```

Session files store A2A `Message` objects for the latest completed exchange, not full transcripts.

### Environments

```bash
aio environment production
aio environment staging
aio environment development
aio env staging
aion-chat environment staging
aion-chat env development
```

Environment commands switch the selected Aion service environment used by `aio login`, `/login`, and registry-backed agent discovery. They do not change the A2A endpoint supplied with `--url`.

These commands are intentionally hidden: `aio environment ...`, `aio env ...`, `aion-chat environment ...`, and `aion-chat env ...` do not appear in command-line `--help`, and `/environment` and `/env` do not appear in the composer slash command picker. They are still executable when typed exactly.

## Development

```bash
npm install
npm run graphql:codegen
npm run dev -- --url http://localhost:8000
```

Run the UI directly from `libs/aion-chat-ui` when you want to work on the Ink/React interface itself. Pass an A2A endpoint with `--url` when you want agent discovery and chat connection.

The GraphQL schema used by this package lives at `src/graphql/chat-client-schema.graphql`. Operations live under `src/graphql/operations/`, and generated TypeScript operation types are committed under `src/graphql/generated/`. Regenerate them with `npm run graphql:codegen` after schema or operation changes.

Use `npm run dev`, not `node src/cli.tsx` or `node src/app.tsx`. The source tree uses TypeScript files with `.js` import specifiers, so it must be run through `tsx` in development or through the built `dist/cli.mjs` bundle.

Set `AION_CHAT_SKIP_UPDATE_CHECK=1` or `AION_CHAT_UPDATE_CHECK=0` to skip the startup update prompt while developing.

If you want to test the integrated Python entrypoint instead, run `aion chat` from an agent project that depends on your local editable `aion-cli`. In that flow, the Python launcher uses `libs/aion-chat-ui/dist/cli.mjs`, so rebuild after UI changes:

```bash
npm run build
npm run stage:python
```

Then in the agent project, run `poetry run aion chat`.

## Build

```bash
npm run build
npm run compile
npm run stage:python
```

- `npm run build` produces a Node-compatible bundle in `dist/cli.mjs`.
- `npm run compile` additionally produces macOS Bun executables.
- `npm run stage:python` copies any available build artifacts into `libs/aion-cli/src/aion/cli/bin/` for packaging and local launch tests.

## Release Flow

The package is published by GitHub Actions when a GitHub release is published for the repository. The workflow lives at `.github/workflows/publish-aion.yml` and works from `libs/aion-chat-ui`.

### Release steps

1. Update `libs/aion-chat-ui/package.json` with the next version.
2. Merge the version change to the branch you release from.
3. Create a GitHub release whose tag matches the package version, for example `v0.2.0`.
4. Publish the release.

When the release is published:

- normal releases publish to npm with the `latest` dist-tag
- prereleases publish to npm with the `next` dist-tag

That gives you a simple update channel split without changing the package name.

### Local verification before cutting a release

```bash
cd libs/aion-chat-ui
npm ci
npm test
npm run build
npm pack --dry-run
```
