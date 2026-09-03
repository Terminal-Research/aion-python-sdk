# Publishing the Python packages to PyPI

The monorepo's ten libraries depend on each other by git reference, which no
index accepts. What is published instead are five *fat* distributions under the
`aionto-` name, each carrying the sources of the libraries it bundles:

| Distribution | Bundles | Modules it installs | Depends on |
|---|---|---|---|
| `aionto-sdk` | aion-core, aion-api-client, aion-db, aion-mcp, aion-server, aion-sdk | `aion.core`, `aion.api`, `aion.db`, `aion.mcp`, `aion.server`, `aion.proxy`, `aion.cli` + the `aion` script | — |
| `aionto-langgraph-authoring` | aion-authoring-langgraph | `aion.langgraph.authoring` | `aionto-sdk` |
| `aionto-langgraph-server` | aion-server-langgraph | `aion.langgraph.server` | `aionto-langgraph-authoring` |
| `aionto-adk-authoring` | aion-authoring-adk | `aion.adk.authoring` | `aionto-sdk` |
| `aionto-adk-server` | aion-server-adk | `aion.adk.server` | `aionto-adk-authoring` |

The internal `aion-*` names are never published. What a distribution carries is
declared in its own `distributions/<name>/pyproject.toml`, under
`[tool.aion-dist] bundles`.

### Where the version comes from

Nothing under `distributions/` holds a version number. All five are released
at the version of `libs/aion-sdk`, the package at the centre of the SDK, so a
release is prepared by bumping that one file and there is no second copy of the
number to drift from the first.

Because the version is derived, a distribution's `pyproject.toml` declares
`dynamic = ["version"]` and lists its `aionto-*` siblings without a specifier.
The build writes both into the staged copy it hands to Poetry, so the published
metadata carries an exact `==` pin between the five.

The five are released together at one version. They are one namespace tree cut
from one commit: a version skew between them installs two halves of different
releases into the same `aion` package. The workflow refuses to publish if the
release tag does not match `libs/aion-sdk`.

### Which README each package ships

The PyPI page of a distribution that bundles one library is that library's
README — `libs/<library>/README.md` — because that page is already written and
already describes exactly what the package installs. A distribution bundling
several must name its page in `[tool.aion-dist] readme`, a path relative to the
repository root: `aionto-sdk` bundles six and fronts the whole SDK, so it names
the repository README.

Relative Markdown links are rewritten to absolute GitHub URLs, resolved against
the directory the README came from. That base matters: a link like
`../aion-authoring-langgraph/README.md` in `libs/aion-server-langgraph/README.md`
points at a sibling library, and resolving it against the repository root
instead would publish a broken link.

The build prints the file it used for each distribution:

```
[DIST] aionto-adk-server 0.1.0 (from aion-server-adk): staging aion-server-adk
  [OK] long description from libs/aion-server-adk/README.md
```

## One-time setup

Publishing uses PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/):
GitHub Actions authenticates with a short-lived OIDC token and the repository
holds no PyPI credentials at all.

1. On pypi.org, under **Account → Publishing**, add a *pending publisher* for
   each of the five project names — `aionto-sdk`,
   `aionto-langgraph-authoring`, `aionto-langgraph-server`,
   `aionto-adk-authoring`, `aionto-adk-server` — with:
   - Owner: `Terminal-Research`
   - Repository: `aion-python-sdk`
   - Workflow: `publish-python.yml`
   - Environment: `pypi`

   A pending publisher is what claims a name that has never been uploaded to;
   it becomes an ordinary publisher on the first successful upload. All five
   are needed: trusted publishing matches each uploaded file to a publisher by
   project name.

2. In the GitHub repository, under **Settings → Environments**, create an
   environment named `pypi`. Required reviewers on it are recommended: the
   publish job then waits for a human before the upload, and every upload is
   final — PyPI does not allow re-uploading a filename, even after a delete.

## Releasing

1. Set the version in `libs/aion-sdk/pyproject.toml` and check the result:

   ```bash
   git diff libs/
   ```

2. Build and verify locally. This is the same pair of commands the workflow
   runs, so a green run here means the release is already proven except for the
   upload itself:

   ```bash
   make dist-build
   make dist-verify
   ```

3. Commit the version change and merge it to `main`.

4. Optionally, run the **Publish Aion Python packages** workflow by hand
   (**Actions → Run workflow**). `workflow_dispatch` runs build and verify and
   uploads the artifacts to the run — the publish job is release-only, so
   nothing reaches PyPI.

5. Create a GitHub release whose tag is `py-v<version>` — for example
   `py-v0.2.0`. The `py-` prefix is what separates this workflow from the npm
   one in `publish-aion.yml`, which publishes `@terminal-research/aion` on
   `v*` tags. The workflow builds, verifies, and publishes all ten files (five
   wheels and five sdists).

6. Check the result from a machine that has never seen the repository:

   ```bash
   pip install aionto-sdk
   aion --help
   ```

## What the build does, and why

`scripts/dist/build.py` assembles each distribution in a temporary directory
**outside** the working tree and runs `poetry build` there. The detour is not
cosmetic: poetry-core asks git which files are ignored and excludes those from
the archives, so a generated, git-ignored `src/` inside the repository would
build a wheel that is empty and looks fine. A staging directory has no `.git`
above it, so what was copied is what ships.

Checks that fail the build rather than the release:

- two bundled libraries staging the same module path;
- an `__init__.py` at `aion/`, `aion/langgraph/` or `aion/adk/` — those levels
  are PEP 420 implicit namespaces shared between distributions, and a regular
  package at any of them hides every sibling's subpackage;
- a file that was staged but did not reach the wheel, which is how a data file
  such as the bundled `cli.mjs` chat UI would silently go missing;
- `libs/aion-sdk` missing or unversioned;
- a distribution that pins its own version instead of deriving one.

`scripts/dist/verify.py` then runs `twine check` over all ten archives and
installs the wheels into two throwaway virtual environments — one LangGraph
stack, one ADK stack, deliberately not together, since no user installs both
and their constraints need not co-resolve. Each environment imports every
module the stack should provide and runs `aion --help`. `--sdist` repeats one
of them from the `.tar.gz`, proving the sdists rebuild.

TestPyPI is deliberately not part of this: several dependencies
(`a2a-sdk`, `google-adk`, `asgi-proxy-lib`) do not exist there, so the install
would need mixed indexes and a second set of publishers, and would prove less
than the local environments already do.

## Dependencies of the fat distributions

The requirements in `distributions/*/pyproject.toml` are the union of the
bundled libraries' external dependencies, written by hand and reviewed at
release time. Two of them are load-bearing beyond what the libraries declare:

- `a2a-sdk[http-server,telemetry,encryption]>=1.1.2,<1.2.0` — the libraries pin
  `1.1.2` exactly, which would conflict with anything else a user installs.
  The upper bound stays at the minor, because `aion.server` imports a2a-sdk
  internals that minor releases move.
- `asgi-proxy-lib==0.2a0` — a pre-release. `aion-mcp` declares it as `*`, which
  pip will not resolve to a pre-release; the pin is required.

`ariadne-codegen` is a development dependency, not a runtime one: the GraphQL
client it generates is committed under
`libs/aion-api-client/src/aion/api/gql/generated/`, and nothing imports the
generator at runtime.

## If a release is bad

Yank all five distributions at the same version together, not just the one at
fault. They pin each other exactly, so a half-yanked release leaves resolvable
combinations that install a mixture. Then bump to the next patch version and
release again — a yanked or deleted version's filenames can never be reused.
