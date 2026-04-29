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

`--url` is an A2A endpoint URL used for agent manifest discovery, agent-card resolution, and A2A JSON-RPC calls. The selected Aion environment is separate from `--url`; it identifies the Aion control-plane API used for login and auth configuration.

### Login

```bash
aio login
aion-chat login
```

`aio login` authenticates against the currently selected Aion environment, defaulting to production when no environment has been selected. The login flow opens the WorkOS login screen in the default browser when possible and shows the user code in the terminal.

Inside the composer, `/login` is visible in the slash command picker and runs the same login flow.

### Environments

```bash
aio environment production
aio environment staging
aio environment development
aio env staging
aion-chat environment staging
aion-chat env development
```

Environment commands switch the selected Aion control-plane environment used by `aio login`, `/login`, and control-plane API calls. They do not change the A2A endpoint supplied with `--url`.

These commands are intentionally hidden: `aio environment ...`, `aio env ...`, `aion-chat environment ...`, and `aion-chat env ...` do not appear in command-line `--help`, and `/environment` and `/env` do not appear in the composer slash command picker. They are still executable when typed exactly.

## Development

```bash
npm install
npm run dev -- --url http://localhost:8000
```

Run the UI directly from `libs/aion-chat-ui` when you want to work on the Ink/React interface itself. Pass an A2A endpoint with `--url` when you want agent discovery and chat connection.

Use `npm run dev`, not `node src/cli.tsx` or `node src/app.tsx`. The source tree uses TypeScript files with `.js` import specifiers, so it must be run through `tsx` in development or through the built `dist/cli.mjs` bundle.

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
