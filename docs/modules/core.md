# aion-core

Foundation layer for the Aion Python SDK. Contains types, constants, protocols,
and utilities — usable in any context without server infrastructure.

All other Aion packages depend on this one; it has no internal Aion dependencies.

## What's inside

| Module | Contents |
|---|---|
| `aion.core.types` | A2A protocol models, enums, request/response types, extension payloads |
| `aion.core.constants` | Shared A2A extension URI constants |
| `aion.core.runtime` | `AionContext` — invocation-scoped context carrier for LangGraph |
| `aion.core.logging` | `get_logger()` — returns `AionLogger` when `aion-server` is installed, stdlib `Logger` otherwise |
| `aion.core.settings` | `BaseEnvSettings`, `ApiSettings`, `api_settings` |
| `aion.core.db` | `DbManagerProtocol` — interface for database manager implementations |
| `aion.core.metaclasses` | `Singleton`, `SingletonABCMeta` |
| `aion.core.utils` | Pydantic, text, and URL helpers |

## Development

```bash
cd libs/aion-core
poetry install
poetry run pytest
```
