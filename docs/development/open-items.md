# Open items

Known gaps of the SDK that remain open on purpose. Every section is one item,
headed by its slug. Code that the item concerns carries `TODO(<slug>)`, and
the reason lives here, not in the comment.

Status is one of:

- **Not supported yet** — the behavior is missing; the section says what to
  do meanwhile.
- **Planned** — the behavior is missing and will be added.
- **Needs verification** — the code is in place but has not been checked
  against a live deployment.

## push-key-reencryption

- **Status:** Not supported yet
- **Code:** `src/aion/server/tasks/push_notifications.py`

`ENCRYPTION_KEY` accepts several comma-separated keys: the first one encrypts,
all of them decrypt. A stored push notification config keeps the key it was
written under; nothing re-encrypts it with the new first key. Keep an old key
in the list while configs encrypted with it are still needed. A config whose
key has been removed cannot be read: a2a-sdk logs it and delivers the task's
other configs without it.

## conversation-history

- **Status:** Planned
- **Code:** none

`aion.core.agent.conversation` reads the tasks of the conversation and the
versions of its artifacts, but has no call that reads its recent messages:
which messages a participant of a shared conversation sees is not settled. An
agent that needs earlier turns reads them from its framework's own state,
which the server keeps per conversation: the LangGraph checkpoint or the ADK
session.
