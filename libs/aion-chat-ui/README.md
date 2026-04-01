# aion-chat-ui

Experimental Ink + React terminal UI for `aion chat2`.

## Goals

- Provide a higher-fidelity terminal chat experience without changing the existing Python `aion chat` flow.
- Use the official JavaScript A2A SDK for transport and message handling.
- Build a standalone executable that `aion-cli` can launch while keeping the UI code isolated in its own subproject.

## Development

```bash
npm install
npm run dev -- --url http://localhost:8000
```

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
