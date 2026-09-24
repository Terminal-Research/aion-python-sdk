# Scenario tests

Unit and integration tests check the parts of the SDK. The scenarios here
check the whole thing: they start a real `aion serve`, talk to the agents
through the proxy with an ordinary A2A client, and assert on what comes back
over the wire. Nothing is mocked, and the harness imports nothing from `aion`
— if a scenario fails, a user of the SDK would have seen the same thing.

No model is ever called: every answer an agent gives here is deterministic,
and the scenarios never touch the Aion platform.

They are a third suite, not part of `make tests-unit`. Every item under this
directory carries the `scenario` marker — the root `tests/conftest.py` puts it
on by directory, so a new module cannot forget it. `make tests-full` runs all
three scenario groups after unit and integration tests. Run relevant scenarios
when a change touches what goes over the wire; `make tests-scenarios-dist`
also checks the built wheel before release.

[SCENARIOS.md](SCENARIOS.md) lists every scenario, what it checks, which
command and deployment it drives, and what happens to it on each framework. It
is generated from the tests and the registries, so it is read, not maintained:
`make scenarios-matrix` rewrites it, and CI fails when it is stale.

## Layout

```
commands.py     the command contract: what to send, what comes back
extensions.py   the two extension URIs this suite owns, and why it owns them
frameworks.py   the frameworks under test, and what each cannot do
harness/        serve, client, recorder, shape, pg, and the payload builders
agents/         one agent package per framework, all answering commands.py
configs/        aion.yaml templates, one per deployment variant
core/           the scenarios, run for every framework
persistence/    the scenarios that need a database under the server
distributed/    the scenarios that need two servers over one database
```

An agent never imports the harness or the tests. The direction is one way:
tests and agents both read `commands.py` and `extensions.py`.

## Running

From the repository root, like every other target:

```bash
make tests-scenarios                       # everything except persistence and distributed
make tests-scenarios TAGS=smoke            # one suite
make tests-scenarios TAGS="daemon events"  # several suites (any of them)
make tests-scenarios FRAMEWORK=langgraph   # one framework
make tests-scenarios ARGS="-x -vv"         # straight through to pytest
make tests-scenarios-persistence           # persistence, against a real database
make tests-scenarios-distributed           # two servers over one real database
make tests-scenarios-dist                  # against the built wheel, in a clean venv
make scenarios-matrix                      # rewrite SCENARIOS.md
```

`make tests-scenarios-persistence` runs the scenarios under `persistence/`,
which restart a server and ask what became of the tasks it was holding, once
per framework. `make tests-scenarios-distributed` runs those under
`distributed/`, which start two servers of one agent over one database and ask
which of them owns a task, may cancel it, and closes what a dead one left
behind. Both use `POSTGRES_TEST_URL` when the environment names one and start a
disposable PostgreSQL otherwise; without a database both skip themselves. The
rest of the scenarios need no database and do not wait for one.

One of them does wait, and the contract is why. A server that shuts down in an
orderly way settles the tasks it was running itself, under the claims it still
holds, so that scenario reads `server_shutdown` the moment the next process is
up. A server that is killed outright settles nothing: its tasks are reclaimed
only when the leases it stopped renewing expire, which is `lease_expired` and
no sooner than the lease allows. The servers here run with
`TASK_OWNERSHIP_LEASE_TTL_SECONDS` at 24 seconds rather than the deployed 60
(`SCENARIO_LEASE_TTL_SECONDS` in `harness/pg.py`, which says why it cannot be
lower), with a reconcile pass every 12, and every wait for a lease is derived
from that TTL. That scenario is still the better part of a minute rather than
seconds, and it is the reason these two groups are not part of
`make tests-scenarios`. `distributed/` waits once more,
for the same reason and only in `test_recovery.py`.

It is also why both groups need a database. In-memory task storage is limited
to the life of one process: it offers no cross-process ownership and no
recovery from a hard crash, because leases, the refusals built on them and the
reaper that settles a dead owner's tasks all belong to the PostgreSQL-backed
store. That is a property of the deployment rather than a gap in the tests -
`tests/unit/server/tasks/test_store_manager_ownership.py` asserts it from the
other side, on the provider the in-memory store selects - so there is no
crash for an in-memory server to recover from, and nothing here pretends
otherwise.

`KEEP_SERVE=1` leaves the servers running after the session and prints the
port, the rendered `aion.yaml` and the log of each — the fastest way to poke at
a deployment a scenario just failed against. `PG_TEST_KEEP=1` does the same for
the database. A failing scenario carries the tail of the server log in its
report either way.

### Which installation is under test

`make tests-scenarios` starts the `aion` of the project environment, so what it
drives is the working tree.

`make tests-scenarios-dist` drives the wheel instead:
`scripts/packaging/scenarios.py` installs `dist/*.whl` with both framework
extras into a virtual environment that inherits nothing, and points the
harness at that environment's `aion` through `SCENARIOS_AION_BIN`. Pytest, the
A2A client and the harness still come from this project — they are tooling,
not the subject. By default this runs the ordinary scenarios; persistence and
distributed scenarios need PostgreSQL and run against the source checkout in
their own CI jobs. It needs a build to run against:

```bash
make dist-build && make tests-scenarios-dist
```

This is the last step of `make release-check`, and the last gate before the
upload in `publish-python.yml`.

## The stream contract

Every scenario builds on the shape of a turn, which is what
[core/test_events.py](core/test_events.py) pins down. A plain turn looks like this:

| # | Event | Meaning |
|---|---|---|
| 1 | `Task` `SUBMITTED` | the stream opens; the task now exists |
| 2 | `TaskStatusUpdateEvent` `WORKING`, no message | the transition into a running task |
| 3 | `TaskStatusUpdateEvent` `WORKING`, with a message | one durable reply; repeated per reply |
| 4 | `Task` terminal | the stream closes on `COMPLETED`, `FAILED`, `CANCELED` or `INPUT_REQUIRED` |

A reply the agent streams arrives ahead of its own step 3: one
`TaskArtifactUpdateEvent` per chunk under the artifact id `aion:stream-delta`
(the first with `append=False`, the rest with `append=True`), followed by the
whole reply as a single `WORKING` status.

An ephemeral event is delivered and then forgotten: the client sees it, task
history never does. There are two shapes of one idea, and which one an agent
produces depends on who emits it. `Thread.typing()` sends a transient artifact
under the id `aion:ephemeral-message`. A server-side extension flags a
`WORKING` status with `aion:ephemeral` in its metadata, which is what
`Ev.ephemeral` in the recorder reads. The ADK adapter does not hold the first
of these today — `frameworks.DIVERGENCES` has the entry and what it does
instead.

## Writing a scenario

```python
@pytest.mark.command("echo")
async def test_echo_answers_with_the_argument(client: ScenarioClient) -> None:
    events = await client.send("echo hello")

    assert_shape(events, [
        task("SUBMITTED"),
        status("WORKING", text=""),
        status("WORKING", text="hello"),
        task("COMPLETED"),
    ])
```

- `client` is an A2A client on the default deployment of the framework the run
  is parametrized over; `server` is the `aion serve` behind it.
- `@pytest.mark.command("<key>")` names the command being driven. It is what
  skips the scenario on a framework listed in `frameworks.UNSUPPORTED`.
- Every scenario carries at least one suite marker from `pyproject.toml` — the
  ones whose description starts with `scenario suite -`. That is what `TAGS=`
  selects on, and what the matrix groups by.
- A scenario on a deployment other than the default carries
  `@pytest.mark.variant("<template>")`, naming the template under `configs/`
  it runs against. Naming one that does not exist is an error at collection.
- The `scenario` marker is added by the root conftest, `asyncio` and
  `timeout` by this directory's; do not write them.
- Expected strings come from `commands.py`, never from a literal in the test —
  the agent builds its answers from the same functions.
- `if framework.name == ...` in a test is not allowed. A difference is either
  an entry in a table in `frameworks.py`, with the reason — `UNSUPPORTED` for
  a command the framework cannot do, `NO_EVENT_ROUTER` for the one authoring
  surface only one adapter has — or a defect to fix. A scenario asks the
  table; it never asks which framework it is.

## Where the line with the unit tests is

A converter rule belongs in `tests/unit/langgraph`, `tests/unit/adk` or
`tests/unit/server`,
where it can be checked by calling the converter. A scenario pins one
observable consequence of that rule on the wire, once, through a real server.

If a check can be written by calling SDK code directly, it is not a scenario:
it is a unit test, and it costs a millisecond instead of a process. What the
scenarios are for is everything in between the pieces — the order events reach
a client in, what a deployment variant changes, what survives being written
down and read back.

## Adding a command

1. Add a `Command` to `COMMANDS` in `commands.py`, with the tags its scenarios
   will carry, and the helper that builds its expected answer.
2. Implement it in every agent under `agents/`, in the behaviour module named
   after its tag.
3. Write the scenarios under `core/`.
4. `make scenarios-matrix`, and commit `SCENARIOS.md` with the change.

Until an agent implements it, that agent answers `not implemented: <key>`, so
the gap shows up as a failure rather than as a wrong menu — and as
`not implemented` in the command table of the matrix.

## Adding a framework

1. Add an agent package under `agents/` that answers the whole
   contract. Its routing table lives in `behaviors.BEHAVIORS`, keyed by
   command; that is where the matrix reads what the agent implements.
2. Add a `Framework` entry to `FRAMEWORKS` in `frameworks.py`, naming the SDK
   extras its server side needs.
3. Add an `UNSUPPORTED` line for anything the framework genuinely cannot do,
   with the reason.
4. `make tests-scenarios` must be green for it, with every skip coming from a
   table in `frameworks.py`.
5. `make scenarios-matrix`, and commit the matrix with the new column.

## Defects a scenario finds

Fix them. The SDK is in this repository, and a scenario that finds a defect is
a scenario, a converter fix and a unit test for the rule that was broken — in
one change. A test is never adjusted to match a broken SDK.

A defect that is knowingly deferred gets
`@pytest.mark.xfail(strict=True, reason="<issue URL>")` and an issue that says
how to reproduce it. Strict, so that the day it is fixed the xfail fails and
the marker goes away; and with the issue in the reason, because the matrix
prints that reason as the scenario's status on every framework.
