# Publishing to PyPI

The repository is one Python project. `poetry build` at the root produces one
wheel and one sdist of **`aionto-sdk`**, and those two files are the whole
release: every `aion.*` subpackage is inside the wheel, and the extras
(`server`, `langgraph-authoring`, `langgraph-server`, `adk-authoring`,
`adk-server`) install third-party libraries, never Aion code.

The version lives in one place, `[project].version` of the root
`pyproject.toml`. There is no second copy to drift from the first, and the
release workflow refuses to build when the release tag does not match it.

## One-time setup

Publishing uses PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/):
GitHub Actions authenticates with a short-lived OIDC token and the repository
holds no PyPI credentials at all.

1. On pypi.org, under **Account → Publishing**, add a *pending publisher* for
   the project name `aionto-sdk`:

   - Owner: `Terminal-Research`
   - Repository: `aion-python-sdk`
   - Workflow: `publish-python.yml`
   - Environment: `pypi`

   A pending publisher is what claims a name that has never been uploaded to;
   it becomes an ordinary publisher on the first successful upload. The
   workflow file name is matched literally: a publisher registered against a
   differently named workflow refuses the OIDC exchange. The environment field
   works the other way round — left empty, shown as `(Any)`, it refuses
   nothing and accepts a token minted in any environment of that workflow.
   Naming `pypi` there is what makes the environment below, the one with the
   reviewers, the only place an upload can come from.

2. In the GitHub repository, under **Settings → Environments**, create an
   environment named `pypi` and give it required reviewers: the publish job
   then waits for a human before the upload, and every upload is final — PyPI
   does not allow re-uploading a filename, even after a delete.

Create the environment, reviewers and all, before the first release rather than
after it. A missing environment does not hold the job back: GitHub creates an
environment the first time a workflow names one, with no protection rules on
it, and the upload goes through with no reviewer asked. The gate exists only if
the environment was there, with its reviewers, before the release that first
uses it.

## Releasing

1. Set the version in the root `pyproject.toml` and check the result:

   ```bash
   git diff pyproject.toml
   ```

2. Build and verify locally. These are the same three commands the workflow
   runs, so a green run here means the release is proven except for the upload
   itself:

   ```bash
   make dist-build
   make dist-check
   make dist-smoke
   ```

   `dist-smoke` builds six clean virtual environments and installs into them,
   so it takes a minute or two. It uses whatever `python3` is on the path;
   `make dist-smoke SMOKE_ARGS="--python 3.12"` pins it to the version the
   release is built with.

3. Commit the version change and merge it to `main`.

4. Optionally, run **Publish Aion Python package** by hand (**Actions → Run
   workflow**). `workflow_dispatch` runs build, check and smoke and uploads
   the two files to the run as an artifact — the `publish` job is release-only,
   so nothing reaches PyPI.

5. Create a GitHub release whose tag is `py-v<version>` — for example
   `py-v0.2.0`. The `py-` prefix is what separates this workflow from the npm
   one in `publish-aion.yml`, which publishes `@terminal-research/aion` on
   `v*` tags; each of the two skips the other's tags by that prefix.

   A pre-release goes out the same way. The tag carries the PEP 440 suffix the
   version has — `py-v0.1.0rc1` for `0.1.0rc1` — and the GitHub release is
   marked as a pre-release; the workflow does not read that flag and behaves
   exactly as it does for a final version, tag-against-version check included.
   What does change is what installers do with the result: while the project
   has no final version on PyPI at all, `pip install aionto-sdk` and
   `uv add aionto-sdk` both resolve to the release candidate without being
   asked for `--pre`, because there is nothing else to resolve to. Once a final
   version exists, pre-releases become opt-in again.

6. Check the result from a machine that has never seen the repository:

   ```bash
   pip install aionto-sdk
   aion --help
   ```

## What the checks do, and why

`scripts/packaging/check.py` (`make dist-check`) reads `dist/` and the root
manifest and nothing else, so what it inspects is exactly what would be
uploaded:

- exactly one wheel and one sdist, at the manifest's version — which is why
  `dist-build` empties `dist/` first;
- every public import path present in the wheel, and no `__init__.py` at
  `aion/`, `aion/langgraph/` or `aion/adk/`. Those three levels are PEP 420
  implicit namespaces, and a regular package at any of them would hide the
  subpackages of anything else sharing the namespace — the behaviour evolution
  toolkit does;
- the bundled `aion/cli/bin/cli.mjs` chat client present in the wheel — a data
  file is exactly what goes missing without anyone noticing — and the sdist
  carrying as many Python files as the wheel, so a wheel rebuilt from it would
  not differ from the one shipped beside it;
- the composite extras still being the unions they claim to be. They are
  written out in full rather than as self-references (Poetry's solver cannot
  install a self-referential extra of the root project), so nothing else
  notices when a dependency is added to `server` and not to `langgraph-server`;
- the written-out expansion of `a2a-sdk[http-server,telemetry,encryption]`
  against a2a-sdk's own metadata — skipped when a2a-sdk is not installed, which
  is why the release job installs it;
- `twine check` over both files.

`scripts/packaging/smoke.py` (`make dist-smoke`) then installs the built files
into six clean virtual environments — base, `[server]`, `[langgraph-server]`,
`[adk-server]`, `[langgraph-server,adk-server]`, and one from the sdist — and uses each one: imports
the subpackages that environment should have, runs `aion --help`, asks plugin
discovery which frameworks loaded, and asserts that the libraries the extra did
*not* buy are absent. The negative checks are the point: a base install that
quietly has `fastapi` in it proves nothing.

TestPyPI is deliberately not part of this. Several dependencies (`a2a-sdk`,
`google-adk`, `asgi-proxy-lib`) do not exist there, so an install would need
mixed indexes and a second publisher, and would prove less than the local
environments already do.

## If a release is bad

Yank the version on PyPI, then bump to the next patch and release again. A
yanked version stays resolvable for anything that already pinned it and
disappears from fresh resolution, which is the outcome wanted; deleting it
instead breaks those pins.

Either way the version number is spent. PyPI never allows a filename to be
reused, not after a yank and not after a delete, so `0.2.1` is the fix for a
bad `0.2.0` — there is no second `0.2.0`.
