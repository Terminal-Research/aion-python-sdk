# `aion chat` Feature Parity Tracking

This document tracks the feature parity of the Ink-based `aion chat` experience.

## Shipped In Prototype

- [x] Direct endpoint connection via `--url` and `--host`
- [x] Default local proxy discovery via `http://localhost:8000`
- [x] Proxy-aware routing via `--agent-id`
- [x] Auto-connect to agent when only one is discovered via `--host` / `--url`
- [x] Root manifest discovery from `/.well-known/manifest.json`
- [x] `@agent-id` composer selection with autocomplete
- [x] Streaming A2A task consumption
- [x] Persistent slash-menu request and response modes
- [x] Optional push-notification webhook listener
- [x] Custom header injection
- [x] Bearer token convenience flag
- [x] Aion distribution and traceability metadata on outgoing requests
- [x] Python launcher command: `aion chat`
- [x] File attachment via prompt flow, absolute/relative paths, and `@file:` autocomplete
- [x] `/exit` slash command to quit the chat

## Still Missing For Full Parity

- [ ] Explicit `--session` reuse across launches
- [ ] `--history` task-history inspection
- [ ] `--extensions` / `X-A2A-Extension`
- [ ] Full rich markdown parity with the legacy client output
- [ ] Rich tool-call timeline rendering in the status bar
- [ ] Linux and Windows packaged binaries
- [ ] Automatic discovery of the latest `aion serve` proxy URL

## Notes

- Update this file whenever `chat` gains or drops a user-facing capability.
