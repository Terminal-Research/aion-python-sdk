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

### Interactive Markdown and Scrollback

Interactive agent responses render Markdown with terminal-native styles for
headings, emphasis, block quotes, lists, fenced code, links, and tables. While
a response is streaming, its unfinished line remains literal until a newline
arrives; the complete response is rendered when the stream closes.

The composer uses Aion's primary color for its prompt and displays the
terminal's native cursor at the current insertion point, including when the
draft wraps onto another row. Cursor shape and blinking follow the terminal's
own configuration. Left and Right move by grapheme, Up and Down move between
composer rows while preserving the preferred column, and Home and End move to
the current row boundary. When a suggestion menu is open, Up and Down continue
to navigate that menu.

Finalized transcript entries are emitted to terminal scrollback and removed
from Ink's mutable layout. The current streaming exchange remains dynamic so it
can still be appended to or replaced. `/clear` resets the chat context, requests
that an interactive terminal erase its visible screen and saved scrollback,
then remounts the fresh home screen. Terminal support varies; when honored, the
request clears the terminal's entire buffer, including output from before Aion
Chat started. Non-interactive output and `TERM=dumb` skip the terminal request
but still reset the logical transcript and context.

### Login

```bash
aio login
aion-chat login
```

`aio login` signs in to the currently selected Aion environment, defaulting to production when no environment has been selected. The login flow opens the Aion sign-in page in the default browser when possible and shows the user code in the terminal. If your account needs additional setup, the CLI opens the appropriate Aion web app page after sign-in.

Inside the composer, `/login` is visible in the slash command picker and runs the same login flow.

If a session expires from inactivity or its credentials are rejected, registry discovery displays a system message asking you to run `/login` again. It does not open browser sign-in automatically; normal silent token refresh remains supported while the session is valid.

### Updates

When an interactive chat session starts, `aio` checks npm for the latest published version. If a newer version is available, it links to that version's GitHub release notes and asks whether to update globally, update in the current project, skip once, or skip until the next version. Choosing an update option runs the npm command and exits; start `aio` again after the install completes.

### Agent Sources and Sessions

Agent sources are discovered per selected Aion environment. Every environment includes a default local source at `http://localhost:8000`; this default is silent when no local server is running. When you are logged in, the selected Aion environment can also provide registry-backed agents with an A2A or AionChat distribution for that account. An identity exposed through both types appears once. Passing `--url` adds an explicit source for that run. Explicit URLs are resolved as a manifest first and then as a direct agent card.

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

GraphQL code generation requires Node.js 22.15 or later. Release CI uses 22.23.3.
The published CLI still supports Node.js 22.0 or later.
This package pins `graphql@16.14.2`, the same version as the React chat library
and Aion frontend. Keep these versions aligned when you update GraphQL.
The `graphql-ws` override keeps the generator's transport at the same `6.3.0`
version as those clients.

### Credential Integration

`SourceCredentials` is the shared fetch boundary for discovery, interactive
connections and headless requests. It pins each credential to the configured
source origin and refuses redirects. The registry keeps account authentication;
direct sources without explicit credentials use `AnonymousSession`. Guest
lifecycle and keychain records are separate from WorkOS refresh tokens. The
Python helper exposes `get-session` and `set-session` using `sessionKey` and
`session`, never the account `refreshToken` field.

Guest records use the versioned scope from the caller-authentication spec:
control-plane API URL, environment, stable source key and destination origin.
`credentialScope` in the agent index is a non-secret local-history discriminator,
not an authorization assertion. It prevents a replacement guest or different
account from restoring an old active context. No history or access is migrated.

See [Authentication And Guest Sessions](https://docs.aion.to/tools/aion-chat#authentication-and-guest-sessions)
for user-facing behavior. Client tests use controlled receivers and fake
keychains; they do not establish server-side token verification or ownership
enforcement. Those are implemented separately in the SDK runtime phase.

### Local Workflow

```bash
npm install
npm run graphql:codegen
npm run dev -- --url http://localhost:8000
```

Run the UI directly from `libs/aion-chat-ui` when you want to work on the Ink/React interface itself. Pass an A2A endpoint with `--url` when you want agent discovery and chat connection.

The GraphQL schema used by this package lives at `src/graphql/chat-client-schema.graphql`. Operations live under `src/graphql/operations/`, and generated TypeScript operation types are committed under `src/graphql/generated/`. Regenerate them with `npm run graphql:codegen` after schema or operation changes.

Sync that file manually from the merged Aion API checkout's
`src/main/resources/static/chat-client-schema.graphql`, produced by its
`generateChatClientSchema` exporter. Do not use the full `schema.graphql` or
the separate `sdk-schema.graphql` export. The chat subset contains login,
current-user, identity catalog/detail, and health queries, plus `a2aRpc` and
`conversationUpdates` subscriptions; it has no mutations. After syncing, run
`npm run graphql:codegen`, `npm test`, and `npm run prepare:python` to validate
the operations and refresh both CLI bundles. Updating the local schema does
not update a running backend; it must also serve the matching contract.

Use `npm run dev`, not `node src/cli.tsx` or `node src/app.tsx`. The source tree uses TypeScript files with `.js` import specifiers, so it must be run through `tsx` in development or through the built `dist/cli.mjs` bundle.

Set `AION_CHAT_SKIP_UPDATE_CHECK=1` or `AION_CHAT_UPDATE_CHECK=0` to skip the startup update prompt while developing.

To test the integrated Python entrypoint, run `aion chat` from an agent project that uses the local editable `aionto-sdk`. In a source checkout with Node available, the Python launcher prefers `libs/aion-chat-ui/dist/cli.mjs`, so rebuild after UI changes:

```bash
npm run build
```

Then run `poetry run aion chat` in the agent project. Before building a Python wheel, run `npm run stage:python` to copy the built bundle into the SDK's `src/aion/cli/bin/`.

## Build

```bash
npm run build
npm run compile
npm run stage:python
```

- `npm run build` produces a Node-compatible bundle in `dist/cli.mjs`.
- `npm run compile` additionally produces macOS Bun executables.
- `npm run stage:python` copies available build artifacts into the repository's `src/aion/cli/bin/` for Python packaging.

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

Agent capabilities come from the selected A2A Agent Card. The bundled GraphQL
schema also includes `a2aAgentCardUrl` for clients that resolve a distribution
target before fetching its card; it is not a separate capability flag.

Explicit `/clear` creates and stores a new context ID before sending a unary
Welcome Message Extension request when the selected card supports it. Welcome
completion is independent of the foreground request. Reconnect and context
restoration do not dispatch it, and failures are not retried. A first user
prompt creates its context without a separate welcome. Session saves merge
messages by ID so concurrent welcomes cannot erase ordinary replies.
