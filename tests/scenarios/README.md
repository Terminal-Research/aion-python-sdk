# Scenario tests

Unit and integration tests check the parts of the SDK. The scenarios here
check the whole thing: they start a real `aion serve`, talk to the agents
through the proxy with an ordinary A2A client, and assert on what comes back
over the wire. Nothing is mocked, and the harness imports nothing from `aion`
— if a scenario fails, a user of the SDK would have seen the same thing.

No model is ever called: every answer an agent gives here is deterministic,
and the scenarios never touch the Aion platform.

They are a third suite, not part of `make tests`. Every item under this
directory carries the `scenario` marker — `conftest.py` puts it on, so a new
module cannot forget it — and all three test targets exclude it. Run them when
a change touches what goes over the wire, and before a release, where
`make scenarios-dist` is a step of the gate.

[SCENARIOS.md](SCENARIOS.md) lists every scenario, what it checks, which
command and deployment it drives, and what happens to it on each framework. It
is generated from the tests and the registries, so it is read, not maintained:
`make scenarios-matrix` rewrites it, and CI fails when it is stale.

## Layout

```
commands.py     the command contract: what to send, what comes back
frameworks.py   the frameworks under test, and what each cannot do
harness/        serve, client, recorder, shape, pg
agents/         one agent package per framework, all answering commands.py
configs/        aion.yaml templates, one per deployment variant
core/           the scenarios, run for every framework
```

An agent never imports the harness or the tests. The direction is one way:
tests and agents both read `commands.py`.

## Running

From the repository root, like every other target:

```bash
make scenarios                        # everything except persistence
make scenarios TAGS=smoke             # one suite
make scenarios TAGS="daemon events"   # several suites (any of them)
make scenarios FRAMEWORK=langgraph    # one framework
make scenarios ARGS="-x -vv"          # straight through to pytest
make scenarios-pg                     # persistence, against a disposable PostgreSQL
make scenarios-dist                   # against the built wheel, in a clean venv
make scenarios-matrix                 # rewrite SCENARIOS.md
```

`make scenarios-pg` collects nothing today: the harness (`harness/pg.py`) and
the target are in place, the persistence suite is not written yet.

`KEEP_SERVE=1` leaves the servers running after the session and prints the
port, the rendered `aion.yaml` and the log of each — the fastest way to poke at
a deployment a scenario just failed against. `PG_TEST_KEEP=1` does the same for
the database. A failing scenario carries the tail of the server log in its
report either way.

### Which installation is under test

`make scenarios` starts the `aion` of the project environment, so what it
drives is the working tree.

`make scenarios-dist` drives the wheel instead:
`scripts/packaging/scenarios.py` installs `dist/*.whl` with both framework
extras into a virtual environment that inherits nothing, and points the
harness at that environment's `aion` through `SCENARIOS_AION_BIN`. Pytest, the
A2A client and the harness still come from this project — they are tooling,
not the subject. It needs a build to run against:

```bash
make dist-build && make scenarios-dist
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
whole reply as a single `WORKING` status. An ephemeral status — a typing
indicator — is streamed with `aion:ephemeral` in its metadata and never reaches
task history.

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
- The `scenario`, `asyncio` and `timeout` markers are added by the conftest;
  do not write them.
- Expected strings come from `commands.py`, never from a literal in the test —
  the agent builds its answers from the same functions.
- `if framework.name == ...` in a test is not allowed. A difference is either
  an entry in `UNSUPPORTED`, with the reason, or a defect to fix.

## Where the line with the unit tests is

A converter rule belongs in `tests/langgraph`, `tests/adk` or `tests/server`,
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
4. `make scenarios` must be green for it, with every skip coming from
   `UNSUPPORTED`.
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
