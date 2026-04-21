# aion-chat-ui

Standalone terminal chat UI for Aion agents. This subproject is published to npm as `@terminal-research/aion` and installs the `aio` executable with an `aion-chat` alias.

## Install

`aio` is the standalone Node entrypoint. `aion-chat` is available as an alias.

```bash
npm install -g @terminal-research/aion
aio https://agent.example.com
aio --agent-id my-agent --url http://localhost:8000
aion-chat --agent-id my-agent --url http://localhost:8000
```

## Development

```bash
npm install
npm run dev -- --url http://localhost:8000
```

Run the UI directly from `libs/aion-chat-ui` when you want to work on the Ink/React interface itself. The default endpoint is `http://localhost:8000`, but you can pass a different endpoint with `--url`.

Use `npm run dev`, not `node src/cli.tsx` or `node src/app.tsx`. The source tree uses TypeScript files with `.js` import specifiers, so it must be run through `tsx` in development or through the built `dist/cli.mjs` bundle.

If you want to test the integrated Python entrypoint instead, run `aion chat2` from an agent project that depends on your local editable `aion-cli`. In that flow, the Python launcher uses `libs/aion-chat-ui/dist/cli.mjs`, so rebuild after UI changes:

```bash
npm run build
npm run stage:python
```

Then in the agent project, run `poetry run aion chat2`.

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

### One-time setup

1. Create or confirm the npm organization scope `@terminal-research`.
2. If npm requires the package record to exist before you can attach the trusted publisher, seed `@terminal-research/aion` once with a maintainer-owned manual publish, then switch publishing over to GitHub Actions.
3. In npm package settings for `@terminal-research/aion`, configure a trusted publisher for this GitHub repository and the `publish-aion.yml` workflow.
4. Keep the workflow's `id-token: write` permission enabled so GitHub Actions can mint the OIDC token during publish.

Trusted publishing removes the need for an `NPM_TOKEN` secret and enables provenance on publish. The workflow upgrades to `npm@^11.5.1` because current npm trusted publishing requires npm 11.5.1 or newer.

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
