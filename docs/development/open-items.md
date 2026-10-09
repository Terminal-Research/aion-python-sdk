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

## codex-aion-provider

- **Status:** Needs verification
- **Code:** `src/aion/server/agent/execution/extensions/evolution/tools_factory.py`

With `CODEX_PROVIDER=aion`, behaviour evolution runs Codex against the Aion
model service's Responses endpoint, with a fresh deployment token per call and
the request's callback attribution. The unit tests cover the wiring; a run
against a live deployment has not been checked yet, including whether the
signed usage-attribution carrier stays valid for the length of a long run.
Until then, `local_session` and `custom` are the providers known to work.

## conversation-history

- **Status:** Not supported yet
- **Code:** `src/aion/core/agent/invocation/thread.py`

`Thread.history()` is meant to return the recent messages of the
conversation. It returns an empty list and logs a warning. An agent that needs
earlier turns reads them from its framework's own state, which the server
keeps per conversation: the LangGraph checkpoint or the ADK session.
