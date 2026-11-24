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

- **Status:** Not supported yet
- **Code:** `src/aion/server/files/storage/backends/aion.py`

A stored file's part keeps the file's address, which with the Aion backend
works only with authorization, and the server hands out a download link for
it (`aion.server.files.links`): in what it answers with and pushes, and for
the files an agent reads. The Files API grants a link to the file's owner -
the identity of the usage attribution it was stored under - and refuses the
deployment's daemon (HTTP 403). A message and a read of a task or a context
ask with the attribution they activated, and a turn's push deliveries with
that of the request whose turn it is. An answer that activated none - a read
from a client that sends no attribution, the push of an abandoned task's
settlement or of a cancel for the deletion of a context whose task runs on
another server - asks on behalf of the daemon, and so is refused; so is a
caller the file does not belong to. Meanwhile those
answers carry the part's address, which does not download without
authorization; the owner gets a working link by reading the task with its
attribution. A refused link costs nothing more: the refusal is logged with
the file's id, and the file version is not asked about again on the same
behalf for a minute.

## drop-aion-adk-artifact-versions

- **Status:** Planned
- **Code:** `src/aion/adk/server/tables.py`

The ADK artifact service records the version numbers it gives in
`aion_adk.adk_artifact_versions`, beside the ADK sessions, and the ADK
migrations copy the rows of `aion.adk_artifact_versions` (SDK revision `010`)
into it once. The table in the `aion` schema is no longer read or written; it
stays for one release, for the servers of the previous version during a rolling
upgrade and for a rollback onto the migrated database. The next release drops
it with an SDK revision listed in `TWO_RELEASE_REMOVALS`, and the copy step
stays, finding nothing to copy.
