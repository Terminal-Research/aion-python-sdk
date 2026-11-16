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

## file-link-authority

- **Status:** Needs verification
- **Code:** `src/aion/server/files/storage/backends/aion.py`

A stored file's download link is issued again once the one its part holds
has expired (`aion.server.files.links`): for what the server answers with
and pushes, and for the files an agent reads. A new link is asked for in a
scope other than the one the file was uploaded in - an answer to `GetTask`
or a push delivery runs outside any agent turn and carries no callback
attribution, so Aion acts as the deployment's daemon, and a later turn can
carry another attribution than the turn that stored the file. Whether the
Files API grants a link in each of those scopes has not been confirmed
against the live service. Meanwhile a link that is not issued costs
nothing: the part leaves with the link it was stored with, the refusal is
logged with the file's id, and the file version is not asked about again
for a minute.
