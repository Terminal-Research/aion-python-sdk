# Releasing the Aion Python SDK

The repository is one PyPI project, `aionto-sdk`: one wheel and one sdist
built from one commit on `main`, with every `aion.*` subpackage inside and
extras that only add third-party libraries. A GitHub Release with a `py-v*`
tag is what publishes it: `.github/workflows/publish-python.yml` builds,
checks and uploads after a reviewer approves. Nothing is published from a
laptop, and the repository holds no PyPI credentials.

## Releasing

Make sure that:

- you are on `main`, and it is what is on GitHub: `git checkout main && git pull`;
- `[project].version` in `pyproject.toml` is the version you mean to release,
  and it is not on PyPI yet: <https://pypi.org/project/aionto-sdk/#history>.

Then:

```bash
make release
```

It checks both of those for you, runs every check a release has to pass, and
asks - here for a `0.2.0` in `pyproject.toml`:

```text
Release aionto-sdk 0.2.0 as py-v0.2.0 (final release). Are you sure? [y/N]
```

`y` creates the GitHub Release `py-v0.2.0`, which starts the workflow. Open
the run under **Actions → Publish Aion Python package**: the `build` job runs
first, then the `publish` job waits in the `pypi` environment. **Review
deployments**, approve. This is the last point at which the release can be
stopped - PyPI never takes a file name twice, not after a delete and not after
a yank, so an approved upload spends the version number for good.

Once the upload has finished, install the new version to check it:

```bash
pip install aionto-sdk
aion --help
```

A pre-release is a version that ends in `rc1`, `b1`, `a1` or `.dev1` -
`0.2.0rc1` - and the same command releases it; the GitHub Release is marked
as a pre-release by itself. Once a final version is on PyPI, installing a
pre-release takes `pip install --pre aionto-sdk`.

To run the checks without releasing anything - on a branch, before the pull
request that sets the version - use:

```bash
make release-check
```

## Details

### What `make release` does

Both commands run `scripts/release.py`, read the version from
`pyproject.toml`, and take none. `make release` goes through four steps:

1. **Preflight.** `gh` is logged in; the working tree is clean, on `main`, and
   `HEAD` is `origin/main`; the tag `py-v<version>` exists neither locally nor
   on origin; no GitHub Release has that name; PyPI does not have the version.
   Any of these failing stops the run before a single check is started.
2. **The checks**, one Makefile target each, so any one can be run on its own:

   | Target | What it does |
   |---|---|
   | `make check-env` | Says the working environment is not half-installed. |
   | `make tests` | Unit suite. |
   | `make lint-imports` | The layer contract between subpackages. |
   | `make dist-build` | Empties `dist/`, builds the wheel and the sdist. |
   | `make dist-check` | Reads `dist/` against the packaging contract, then `twine check`. |
   | `make dist-smoke` | Installs the built files into nine clean venvs and uses each one. |
   | `make tests-scenarios-dist` | Runs the scenario suite against the built wheel in a clean venv. |

   The last four are what the release workflow itself runs, so green here
   means the release is proven except for the upload.
3. **The question.** Anything but `y` stops here. `make release YES=1` answers
   it, for a run without a terminal; without `YES=1` a run that has no
   terminal to ask on stops instead of assuming.
4. **The release.** Creates the GitHub Release `py-v<version>` on `main` with
   generated notes; a pre-release version gets the pre-release flag by itself.
   Publishing the release is what starts the workflow that uploads to PyPI.

The release is the last thing it does. A failure anywhere before it stops the
run with the failing step named, and nothing has been spent: no tag, no
release, no version number. `make release-check` is step 2 alone.

`dist-smoke` takes a minute or two and uses whatever `python3` is on the path;
`tests-scenarios-dist` adds about half a minute on top, in an environment of the same
kind. Pin both to the version the release is built with:
`make release-check RELEASE_ARGS="--python 3.12"` (`make release` takes the
same), or `make dist-smoke SMOKE_ARGS="--python 3.12"` and
`make tests-scenarios-dist SCENARIOS_ARGS="--python 3.12"` on their own.

The same release by hand, if ever needed: `gh release create py-v0.2.0
--target main --title py-v0.2.0 --generate-notes`, plus `--prerelease` for a
pre-release; or in the UI, Releases → Draft a new release → tag `py-v0.2.0`,
target `main`, publish.

### Version rules

The version lives in `[project].version` of the root `pyproject.toml`, and the
tag is that version with a `py-v` prefix. The workflow refuses to build when
the two disagree.

| Version | Tag | GitHub Release |
|---|---|---|
| `0.2.0` | `py-v0.2.0` | release |
| `0.2.0rc1` | `py-v0.2.0rc1` | pre-release |

Versions follow [PEP 440](https://peps.python.org/pep-0440/): a pre-release is
`rc1`, `b1` or `a1` with no separator. `0.2.0-rc1` and `0.2.0pr1` are not
versions, and Poetry silently rewrites what it can, after which the tag no
longer matches - `make release` refuses anything that is not the canonical
spelling before it runs a single check.

Whether a version is a release or a pre-release is read off the version, and
nothing else: an `a`, `b`, `rc` or `.dev` segment makes it a pre-release, and
`pip install aionto-sdk` without `--pre` skips it once a final version exists.
There is no separate pre-release flow; the same two commands cut both.

The `py-` prefix separates this workflow from the npm one in
`publish-aion.yml`, which publishes the chat UI on `v*` tags. Each skips the
other's tags.

### Rehearsing the workflow

Optional, and worth it before the first release of a new version whenever the
packaging changed. **Actions → Publish Aion Python package → Run workflow**
builds, checks and smokes on Python 3.12 and uploads the two files to the run
as the `python-dist` artifact. The `publish` job does not run on a manual
dispatch, so nothing reaches PyPI and no version number is spent.

### When a release goes wrong

**A check fails locally**, or `make release` stops in its preflight or at the
question. Nothing has been spent. Fix it and re-run.

**The build job fails on the release.** No upload happened. Delete the release
and its tag, fix, release again with the same version.

**The upload succeeded and the release is bad.** Yank the version on PyPI, then
release the next patch. A yanked version stays resolvable for anything that
already pinned it and disappears from fresh resolution; deleting it breaks
those pins instead. Either way the number is spent - `0.2.1` is the fix for a
bad `0.2.0`, there is no second `0.2.0`.

### What the checks are actually for

- **`dist-check`** reads `dist/` and the manifest and nothing else, so it
  inspects exactly what would be uploaded: one wheel and one sdist at the right
  version, every public import path present, no `__init__.py` at `aion/`,
  `aion/langgraph/` or `aion/adk/` (those three are namespace levels - a
  regular package there would hide the subpackages of anything else sharing the
  namespace), the bundled `cli.mjs` chat client present, the composite extras
  still being the unions they claim to be, and `twine check` over both files.
- **`dist-smoke`** installs into nine clean environments - base, `[server]`,
  `[langgraph-server]`, `[adk-server]`, both together, three partial
  combinations nobody publishes an install line for but somebody will assemble
  (`[server,langgraph-authoring]`, `[server,adk-authoring]` and the two
  authoring toolkits with no server under them), and one from the sdist - and
  uses each: imports what should be there, runs `aion --help`, asks plugin
  discovery which frameworks loaded, and asserts the libraries that extra did
  *not* buy are absent. The negative half is the point: a base install that
  quietly carries `fastapi` proves nothing.
- **`tests-scenarios-dist`** is the one check that runs the product. It installs the
  wheel with both framework extras into one more clean environment and runs
  `tests/scenarios` against it: a real `aion serve` per framework and
  deployment variant, driven through the proxy by an A2A client, asserting on
  what comes back over the wire. `dist-smoke` proves the installation is well
  formed; this proves an agent written against it still behaves. The suite is
  described in `tests/scenarios/README.md`.

TestPyPI is deliberately not part of this. Several dependencies (`a2a-sdk`,
`google-adk`, `asgi-proxy-lib`) do not exist there, so an install would need
mixed indexes and a second publisher, and would prove less than the nine local
environments already do.
