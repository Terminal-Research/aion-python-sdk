# Releasing the Aion Python SDK

The repository is one PyPI project, `aionto-sdk`. A release is one wheel and
one sdist built from one commit on `main`; every `aion.*` subpackage is inside
the wheel, and the extras only ever add third-party libraries.

Publishing an ordinary GitHub Release with a `py-v*` tag is what starts it.
`.github/workflows/publish-python.yml` builds, checks and uploads; a required
reviewer approves the upload. Nothing is published from a laptop, and the
repository holds no PyPI credentials.

## Commands

| Command | What it does |
|---|---|
| `poetry version 0.2.0` | Sets `[project].version`. The only place a version lives. |
| `make check-env` | Says the working environment is not half-installed. |
| `make tests` | Unit suite. |
| `make lint-imports` | The layer contract between subpackages. |
| `make dist-build` | Empties `dist/`, builds the wheel and the sdist. |
| `make dist-check` | Reads `dist/` against the packaging contract, then `twine check`. |
| `make dist-smoke` | Installs the built files into six clean venvs and uses each one. |
| `gh release create py-v0.2.0 --target main --title py-v0.2.0 --generate-notes` | Publishes the release, which starts the workflow. |

The last three are what the release workflow itself runs. Green here means the
release is proven except for the upload.

`dist-smoke` takes a minute or two and uses whatever `python3` is on the path.
Pin it to the version the release is built with:
`make dist-smoke SMOKE_ARGS="--python 3.12"`.

## Version rules

The version lives in `[project].version` of the root `pyproject.toml`, and the
tag is that version with a `py-v` prefix. The workflow refuses to build when
the two disagree.

| Version | Tag |
|---|---|
| `0.2.0` | `py-v0.2.0` |
| `0.2.0rc1` | `py-v0.2.0rc1` |

Versions follow [PEP 440](https://peps.python.org/pep-0440/): a pre-release is
`rc1`, `b1` or `a1` with no separator. `0.2.0-rc1` and `0.2.0pr1` are not
versions, and Poetry silently rewrites what it can, after which the tag no
longer matches.

The `py-` prefix separates this workflow from the npm one in
`publish-aion.yml`, which publishes the chat UI on `v*` tags. Each skips the
other's tags.

## A release, end to end

Releasing `0.1.0rc1`, the first one. Everything before this is on a branch that
is already merged.

**1. Set the version and check it.**

```bash
poetry version 0.1.0rc1
git diff pyproject.toml
```

**2. Run the checks.**

```bash
make check-env && make tests && make lint-imports
make dist-build && make dist-check && make dist-smoke
```

`dist-check` ends with `all checks passed`, `dist-smoke` with six environments
reported `ok`. Anything else stops the release here.

**3. Commit the version and get it on `main`.**

```bash
git add pyproject.toml
git commit -m "Release aionto-sdk 0.1.0rc1"
```

Push, open the pull request, merge it. The tag has to point at a commit that is
on `main`.

**4. Rehearse the workflow.** Optional, and worth it before the first release
of a new version whenever the packaging changed. **Actions → Publish Aion
Python package → Run workflow** builds, checks and smokes on Python 3.12 and
uploads the two files to the run as the `python-dist` artifact. The `publish`
job does not run on a manual dispatch, so nothing reaches PyPI and no version
number is spent.

**5. Publish the release.**

```bash
git checkout main && git pull
gh release create py-v0.1.0rc1 --target main --title py-v0.1.0rc1 --generate-notes --prerelease
```

Drop `--prerelease` for a final version. The same thing in the UI: Releases →
Draft a new release → tag `py-v0.1.0rc1`, target `main`, publish.

**6. Approve the upload.** The `build` job runs first. The `publish` job then
waits in the `pypi` environment. Open the run, **Review deployments**, approve.

This is the last point at which a release can be stopped. PyPI never allows a
filename to be re-uploaded, not after a delete and not after a yank, so an
approved upload spends that version number for good.

**7. Check it from outside.** On a machine that has never seen the repository:

```bash
pip install aionto-sdk
aion --help
```

While the project has no final version on PyPI, this installs the release
candidate without `--pre` — there is nothing else to resolve to. Once `0.1.0`
is out, pre-releases need `pip install --pre aionto-sdk`.

## When a release goes wrong

**A check fails locally.** Nothing has been spent. Fix it and re-run.

**The build job fails on the release.** No upload happened. Delete the release
and its tag, fix, release again with the same version.

**The upload succeeded and the release is bad.** Yank the version on PyPI, then
release the next patch. A yanked version stays resolvable for anything that
already pinned it and disappears from fresh resolution; deleting it breaks
those pins instead. Either way the number is spent — `0.2.1` is the fix for a
bad `0.2.0`, there is no second `0.2.0`.

## What the checks are actually for

- **`dist-check`** reads `dist/` and the manifest and nothing else, so it
  inspects exactly what would be uploaded: one wheel and one sdist at the right
  version, every public import path present, no `__init__.py` at `aion/`,
  `aion/langgraph/` or `aion/adk/` (those three are namespace levels — a
  regular package there would hide the subpackages of anything else sharing the
  namespace), the bundled `cli.mjs` chat client present, the composite extras
  still being the unions they claim to be, and `twine check` over both files.
- **`dist-smoke`** installs into six clean environments — base, `[server]`,
  `[langgraph-server]`, `[adk-server]`, both together, and one from the sdist —
  and uses each: imports what should be there, runs `aion --help`, asks plugin
  discovery which frameworks loaded, and asserts the libraries that extra did
  *not* buy are absent. The negative half is the point: a base install that
  quietly carries `fastapi` proves nothing.

TestPyPI is deliberately not part of this. Several dependencies (`a2a-sdk`,
`google-adk`, `asgi-proxy-lib`) do not exist there, so an install would need
mixed indexes and a second publisher, and would prove less than the six local
environments already do.
