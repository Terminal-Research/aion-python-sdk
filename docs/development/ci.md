# Continuous integration

`.github/workflows/python-ci.yml` runs the full source-checkout gate on every
pull request update, on pushes to `main`, and when started manually. A newer
push to the same pull request cancels its superseded run. The jobs in one run
are independent, so one failing group does not stop the others.

| Job | What it runs |
| --- | --- |
| `unit` | `make tests-unit`, layer contract, scenario matrix, distribution build and check on Python 3.12, 3.13, and 3.14 |
| `integration` | `make tests-integration` with its own PostgreSQL service |
| `scenarios` | Ordinary in-memory scenarios |
| `scenarios-persistence` | Persistence scenarios with its own PostgreSQL service |
| `scenarios-distributed` | Distributed scenarios with its own PostgreSQL service |
| `floors` | Unit tests against the oldest allowed direct dependencies |
| `CI result` | Fails unless every job above succeeded |

`CI result` runs even if a test job fails or is cancelled. This matters because
GitHub may treat a skipped required job as successful. The check accepts no
skipped test group. Each PostgreSQL job has a separate runner and service, so
the groups can run at the same time without sharing a database.

The workflow builds and checks the wheel and sdist. Clean-install smoke tests
and scenarios against the built wheel run in the release workflow; see
`RELEASE.md`. `workflow_dispatch` runs the full source-checkout gate manually,
but it is not a substitute for a required pull request check.

## Require the checks before merging

The workflow alone does not block a merge. Protect `main` after `CI result` has
appeared on a pull request:

1. In **Settings → Rules → Rulesets**, create an active branch ruleset for the
   default branch. Keep the bypass list empty, block deletions and force pushes,
   and require a pull request before merging.
2. Require the `CI result` status check from **GitHub Actions**. Enable
   **Require branches to be up to date before merging**, so the checked pull
   request includes the current `main`. This may require another CI run when
   `main` changes.
3. Verify with a test pull request that a failing `CI result` blocks merging
   and that a direct push to `main` is rejected.

Until the ruleset is active, CI reports failures but does not prevent a merge.
A merge queue is optional. If one is added later, enable the `merge_group`
trigger and run the same full gate for merge groups.
