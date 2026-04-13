# aion-chat-ui

Experimental Ink + React terminal UI for `aion chat2`.

## Goals

- Provide a higher-fidelity terminal chat experience without changing the existing Python `aion chat` flow.
- Use the official JavaScript A2A SDK for transport and message handling.
- Build a standalone executable that `aion-cli` can launch while keeping the UI code isolated in its own subproject.

## Development

```bash
npm install
npm run dev
```

Run the development UI directly from `libs/aion-chat-ui` when you want to work on the
Ink/React interface itself. This does not need to be run from an agent project, but it does
need a reachable A2A endpoint. The default endpoint is `http://localhost:8000`, but you can
pass a different endpoint with `--url`.

```bash
npm run dev -- --url http://localhost:8000
```

Use `npm run dev`, not `node src/cli.tsx` or `node src/app.tsx`. The source tree uses
TypeScript files with `.js` import specifiers, so it must be run through `tsx` in
development or through the built `dist/cli.mjs` bundle.

If you want to test the integrated Python entrypoint instead, run `aion chat2` from an
agent project that depends on your local editable `aion-cli`. In that flow, the Python
launcher uses `libs/aion-chat-ui/dist/cli.mjs`, so rebuild after UI changes:

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

`npm run build` produces a Node-compatible bundle in `dist/cli.mjs`.
`npm run compile` additionally produces macOS Bun executables.
`npm run stage:python` copies any available build artifacts into
`libs/aion-cli/src/aion/cli/bin/` for packaging and local launch tests.
