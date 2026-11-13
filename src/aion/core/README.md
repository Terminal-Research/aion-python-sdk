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
| `aion.core.agent` | `BaseThread`, `BaseMessage`, `User` — the base invocation abstractions (`card`, `message`, `thread`) frameworks build on; `conversation` — the messages, tasks, artifacts and files of the conversation the current turn belongs to; `testing` — a conversation for tests |
| `aion.core.a2a.content` | Reading a message, an artifact or a task: `text_of`, `data_of`, `file_parts`, `conversation_messages`, `answer_text` |
| `aion.core.a2a.files` | The files of a message or an artifact: `files_of`, `ConversationFile`, and `StoredFile`, where the server keeps a stored file |
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
already holds - its messages, its tasks, its artifacts and their files - the
way `emit_*` write a turn's output. The server installs the conversation of
every turn on the turn's runtime context, for the agent's framework and an
executable extension alike, so the functions need no arguments for the
context; outside a turn they raise `ConversationUnavailableError`
(`conversation.is_available()` asks first).

```python
from a2a.types import Role

from aion.core.a2a import UNFINISHED_TASK_STATES
from aion.core.agent import conversation

recent = await conversation.list_messages(limit=20)
asked = await conversation.list_messages(role=Role.ROLE_USER, limit=1)
waiting = await conversation.list_tasks(states=UNFINISHED_TASK_STATES)

reports = await conversation.list_artifacts(name="report.pdf", limit=1)
if reports:
    pdf = await reports[0].read()

spec = await conversation.get_artifact("evolution-spec")
```

`list_*` returns a list, narrowed by its arguments; `get_*` returns one
object, or `None` without it.

| Read | Returns |
| --- | --- |
| `list_messages(role=, task_id=, limit=, offset=)` | The messages, oldest first: of one `Role` (`Role.ROLE_USER` / `Role.ROLE_AGENT`), of one task, the newest `limit` of them, after skipping the `offset` newest. |
| `list_tasks(states=, limit=)`, `get_task(id)`, `get_current_task()` | The tasks, oldest first: in the given `TaskState` values, the newest `limit` of them. `aion.core.a2a` names the groups of states: `TERMINAL_TASK_STATES`, `INTERRUPT_TASK_STATES`, `ACTIVE_TASK_STATES`, `UNFINISHED_TASK_STATES`. |
| `list_artifacts(name=, task_id=, all_versions=, include_sdk=, limit=)` | The latest version of every artifact, newest first, as `ArtifactVersion`. |
| `get_artifact(id, version=)`, `list_artifact_versions(id)` | One artifact by its id: its latest version or the one with the given number; or every version of an id. |

An `ArtifactVersion` gives its content without walking protobuf parts: `text`,
`data`, `files`, and `read()` / `read_text()` for its content as bytes or
text, whether the artifact holds text, an inline file or a stored one. A
stored file is downloaded on the first read and kept on the object.

The address a stored file's part holds is a download link that expires long
before the file is removed. A read does not depend on it: the server reads
the file through a link that works now, issuing a new one for the same file
once the stored one has expired, for as long as the file is stored. A file
that is no longer stored, or one whose link cannot be issued again, raises
`ConversationFileError`. `await file.link()` gives the working link to code
that passes an address on - to a model that takes a file by URL - and
`file.stored` says where the server keeps the file: `file_id`,
`version_id`, `access_expires_at`, `retention_expires_at`; it is None for
an inline file and for an address the server did not store the file under.
A link gives access to whoever holds it, so it is passed on and never
logged.

`aion.core.a2a` reads any message, artifact or task the same way:
`text_of`, `data_of`, `files_of` - its files as `ConversationFile` -
`conversation_messages(task)` and `answer_text(task)`, the text a task
answered with, wherever the task keeps it - a streamed answer, an artifact,
or its last message.

A turn reads the context of its task as the context's holder: every
participant's tasks and messages of a shared conversation, nothing of another
context.

Code that reads the conversation is tested without a server:

```python
from a2a.types import Role

from aion.core.a2a import text_artifact
from aion.core.agent import conversation
from aion.core.agent.testing import InMemoryConversation, use_conversation

async def test_the_draft_is_found():
    earlier = InMemoryConversation()
    earlier.add_task(
        messages=["Write a report", (Role.ROLE_AGENT, "Here is the draft.")],
        artifacts=[text_artifact("Draft", name="report.md")],
    )
    with use_conversation(earlier):
        (draft,) = await conversation.list_artifacts(name="report.md")
        assert draft.text == "Draft"
```

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
`context.extensions.is_active(CRON_EXTENSION_URI_V1)` before treating it as active.
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
