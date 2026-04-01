# `chat2` Feature Parity Tracking

This document tracks the gap between the current Python `aion chat` command and
the experimental Ink-based `aion chat2` experience.

## Shipped In Prototype

- [x] Direct endpoint connection via `--url` and `--host`
- [x] Default local proxy discovery via `http://localhost:8000`
- [x] Proxy-aware routing via `--agent-id`
- [x] Root manifest discovery from `/.well-known/manifest.json`
- [x] `@agent-id` composer selection with autocomplete
- [x] Streaming A2A task consumption
- [x] `--no-stream` fallback path
- [x] Optional push-notification webhook listener
- [x] Custom header injection
- [x] Bearer token convenience flag
- [x] Aion distribution and traceability metadata on outgoing requests
- [x] Experimental Python launcher command: `aion chat2`

## Still Missing For Full Parity

- [ ] Explicit `--session` reuse across launches
- [ ] `--history` task-history inspection
- [ ] File attachment prompt and upload flow
- [ ] `--extensions` / `X-A2A-Extension`
- [ ] Full rich markdown parity with the legacy client output
- [ ] Rich tool-call timeline rendering in the status bar
- [ ] Linux and Windows packaged binaries
- [ ] Automatic discovery of the latest `aion serve` proxy URL

## Notes

- `chat2` intentionally keeps `aion chat` unchanged so both UX paths can be
  evaluated in parallel.
- Update this file whenever `chat2` gains or drops a user-facing capability.
