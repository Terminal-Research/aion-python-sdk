# aion.core

Foundation layer for the Aion Python SDK. Contains types, constants, protocols,
and utilities — usable in any context without server infrastructure.

Always installed: it is part of the base `pip install aionto-sdk`, and no extra
adds anything to it.

All other Aion subpackages depend on this one; it has no internal Aion dependencies.

## What's inside

| Module | Contents |
|---|---|
| `aion.core.exceptions` | `AionError`, the root every SDK exception descends from, and the public ones: `MissingOptionalDependency`, `ConfigurationError`, `AionAuthenticationError`, `AionModelPrincipalError` |
| `aion.core.a2a` | A2A protocol models, enums, request/response types, extension payloads |
| `aion.core.agent` | `BaseThread`, `BaseMessage`, `User` — the base invocation abstractions (`card`, `message`, `thread`) frameworks build on; `conversation` — the history of the conversation the current turn belongs to |
| `aion.core.constants` | Shared A2A extension URI constants |
| `aion.core.runtime` | `AionRuntimeContext` — invocation-scoped context carrier |
| `aion.core.a2a.codes`, `aion.core.a2a.errors` | The error codes the server reserves for executable extensions, and the A2A shape of an extension's error (`{uri}#ExtensionError`) |
| `aion.core.principal` | Canonical caller-ID codec shared by server token subjects, distribution metadata and callback attribution |
| `aion.core.logging` | `AionLogger` / `AionLogRecord` — the logger class every Aion logger is created from, carrying the context fields `aion.server` fills in |
| `aion.core.config` | `aion.yaml` models (`AionConfig`, `AgentConfig`, `AgentSkill`), `AionConfigReader`, the publication collectors (`ConfigurationError` is re-exported here from `aion.core.exceptions`) |
| `aion.core.settings` | `BaseEnvSettings`, `ApiSettings`, `api_settings` |
| `aion.core.db` | `DbManagerProtocol` — interface for database manager implementations |
| `aion.core.http` | `HealthResponse` — the standard response models HTTP endpoints answer with |
| `aion.core.metaclasses` | `Singleton`, `SingletonABCMeta` |
| `aion.core.utils` | Pydantic, text, and URL helpers, plus `missing_extra_error`, which names the extra a missing library belongs to and raises `aion.core.exceptions.MissingOptionalDependency` |

## Conversation history

`aion.core.agent.conversation` reads what the conversation of the current turn
already holds - its tasks, the generation of its id, the versions of an
artifact - the way `emit_*` write a turn's output. The server installs the
history of every turn on the turn's runtime context, for the agent's framework
and an executable extension alike, so the functions need no arguments for the
context; outside a turn they raise `ConversationUnavailableError`.

```python
from aion.core.agent import conversation

spec = await conversation.find_artifact("evolution-spec")
waiting = await conversation.tasks(unfinished=True)
```

A turn reads the context of its task as the context's holder: every
participant's tasks of a shared conversation, nothing of another context.

## Caller IDs

Caller IDs have the format `aion:v1:<PrincipalType>:<unpadded-base64url-id>`.
`Principal.from_subject` checks canonical formatting and round-trips already
encoded subjects unchanged. It does not authenticate a caller: direct callback
attribution may be reported by the deployment, while callback authorization is
resolved independently from its Version credential. Do not infer an Aion user
from a raw provider ID or use a decoded caller as an authorization grant.

## Scheduled Invocations


The optional Cron extension exposes `CronExtensionV1` through
`context.extensions.get(CRON_EXTENSION_URI_V1)`. Check
`context.is_extension_active(AionExtensions.CRON)` before treating it as active.
It includes only UTC `scheduled_at` / `sent_at` instants (wire keys
`scheduledAt` / `sentAt`, with a `Z` suffix); the extension URI marks Cron
origin. No schedule, identifiers, or timezone database lookup are needed.
It does not copy message content or prove the caller's authority. Agent authors
decide whether to use it in prompts or copy its payload into response metadata.
Advertising support does not make Cron attachable to A2A or Aion Chat
distributions; attachment ownership remains a control-plane, Behavior-only rule.

## Development

```bash
poetry install -E langgraph-server -E adk-server --with dev
make tests-unit TEST_PATHS="tests/unit/core"
```


### Welcome message requests

An agent may opt into
`https://docs.aion.to/a2a/extensions/aion/welcome-message/1.0.0` through
`enabled_extensions` in `aion.yaml`. The runtime validates its schema-tagged
`WelcomeRequestPayload` and exposes it through `context.extensions.get(uri)`.
Actual user text takes precedence over welcome intent. The implementation
chooses the greeting and completes through its existing task API; the SDK
adds no greeting generator. Mark each intentional greeting with the URI in
that response message's `extensions`; acknowledgment headers do not add it.
