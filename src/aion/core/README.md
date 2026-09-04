# aion.core

Foundation layer for the Aion Python SDK. Contains types, constants, protocols,
and utilities — usable in any context without server infrastructure.

Always installed: it is part of the base `pip install aionto-sdk`, and no extra
adds anything to it.

All other Aion subpackages depend on this one; it has no internal Aion dependencies.

## What's inside

| Module | Contents |
|---|---|
| `aion.core.a2a` | A2A protocol models, enums, request/response types, extension payloads |
| `aion.core.constants` | Shared A2A extension URI constants |
| `aion.core.runtime` | `AionRuntimeContext` — invocation-scoped context carrier |
| `aion.core.logging` | `AionLogger` / `AionLogRecord` — the logger class every Aion logger is created from, carrying the context fields `aion.server` fills in |
| `aion.core.settings` | `BaseEnvSettings`, `ApiSettings`, `api_settings` |
| `aion.core.db` | `DbManagerProtocol` — interface for database manager implementations |
| `aion.core.metaclasses` | `Singleton`, `SingletonABCMeta` |
| `aion.core.utils` | Pydantic, text, and URL helpers, plus `missing_extra_error`, which names the extra a missing library belongs to |

## Development

```bash
poetry install -E langgraph-server -E adk-server --with dev
make tests ARGS="tests/core"
```
