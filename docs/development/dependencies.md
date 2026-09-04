# Dependencies Management

The repository is one Python project, `aionto-sdk`, with one environment.

## Install

```bash
poetry install -E langgraph-server -E adk-server --with dev
```

The two server extras between them bring every optional dependency — LangGraph, Google ADK, the server
stack — and `--with dev` brings pytest, import-linter, the GraphQL code
generator and twine. The project itself is installed editable, so every
`aion.*` import resolves to `src/` in the working tree and nothing has to be
reinstalled after an edit.

`poetry.lock` is not committed: what a fresh install resolves is what the
manifest says today.

The project's `poetry.toml` turns `installer.re-resolve` back on. Since Poetry
2.3.0 the default is off, and `poetry install` then picks packages out of the
lock file by marker instead of solving. That does not work here: the extras
constrain the same packages differently — Google ADK holds `opentelemetry` and
`mcp` down, LiteLLM holds `openai` down, the base install and the LangGraph
branch hold nothing down — so the lock carries two entries with overlapping
markers for each of those packages, and the installer takes both. Two versions
of one distribution end up unpacked into the same `site-packages`, files on
top of each other. Re-resolving costs a few seconds and gives one version per
package. See [python-poetry/poetry#10971](https://github.com/python-poetry/poetry/issues/10971).

`make check-env` is what says whether the environment came out whole — no
package installed twice, and `pip check` clean:

```bash
make check-env
```

If an install ever leaves a strange environment behind — an import error from
inside a third-party package, a version nothing asked for — do not pick at it
by hand. Delete `.venv` and install again; a half-overwritten package cannot be
repaired in place, and reinstalling one distribution over it leaves the other
one's files where they were.

To see all available commands:

```bash
make help
```

## Changing dependencies

1. Edit the root `pyproject.toml`. A runtime dependency belongs to the extra
   that owns it — `server`, `langgraph-authoring`, `adk-authoring` — or to
   `[project.dependencies]` if the base install needs it. A tool used only
   while developing belongs in `[tool.poetry.group.dev.dependencies]`.
2. The composite extras `langgraph-server` and `adk-server` are written
   out in full rather than referring to their parts, so a dependency added to a
   component extra has to be added to them too. `make dist-check` is what
   catches the copy you forgot — run it after touching extras:

   ```bash
   make dist-build && make dist-check
   ```

3. Re-resolve the environment:

   ```bash
   poetry install -E langgraph-server -E adk-server --with dev
   ```

## Testing changes from a feature branch

Consumers depend on the SDK by git reference:

```toml
aionto-sdk = { git = "https://github.com/Terminal-Research/aion-python-sdk", branch = "main", extras = ["langgraph-server"] }
```

To try a branch before it merges, point that reference at it, push the branch
first, and remember to put `branch = "main"` back before merging:

```toml
aionto-sdk = { git = "https://github.com/Terminal-Research/aion-python-sdk", branch = "features/my-feature", extras = ["langgraph-server"] }
```

There is one reference to change, not ten: the SDK is a single package, so a
branch install cannot pull half of itself from `main`.

To check what an installed release actually gives you — rather than what the
working tree gives you — build and install the distributions into clean
environments:

```bash
make dist-build
make dist-check
make dist-smoke
```

See [RELEASE.md](../../RELEASE.md) for what those three do and how a release
is cut.
