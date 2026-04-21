# Releasing Aion Chat UI

This package is published to npm as `@terminal-research/aion` and installs the
`aio` executable with an `aion-chat` alias. Releases are published by the GitHub
Actions workflow at `.github/workflows/publish-aion.yml` when a GitHub Release is
published.

## Release Branch

Use `main` as the release branch unless the repository adopts a dedicated
`release/*` branch policy later.

The workflow itself does not name a release branch. GitHub releases are tied to
Git tags, and the tag points at the exact commit being released. When creating a
new release in the GitHub UI, choose `main` as the tag target if the tag does not
already exist.

## Version Rules

The release tag must match the version in `libs/aion-chat-ui/package.json`.

Examples:

- `package.json` version `0.2.0` must be released with tag `v0.2.0`
- `package.json` version `0.3.0-beta.1` must be released with tag `v0.3.0-beta.1`

The workflow checks this before publishing. A mismatched tag fails the release
instead of publishing the wrong package version.

## Before Publishing

From the repository root:

```bash
cd libs/aion-chat-ui
npm version 0.2.0 --no-git-tag-version
npm test
npm run build
npm pack --dry-run
```

Commit both version files:

```bash
git add libs/aion-chat-ui/package.json libs/aion-chat-ui/package-lock.json
git commit -m "Release Aion chat UI v0.2.0"
```

Merge that commit to `main` before creating the GitHub Release.

## Create the GitHub Release

In the GitHub UI:

1. Open the repository's Releases page.
2. Choose **Draft a new release**.
3. Enter a tag that matches the package version, such as `v0.2.0`.
4. If GitHub needs to create the tag, choose `main` as the target branch.
5. Use the same version in the release title, such as `v0.2.0`.
6. Mark the release as a prerelease only for preview builds.
7. Publish the release.

Publishing the release triggers the npm publish workflow.

## Publish Channels

The workflow publishes stable GitHub releases to npm with the `latest` dist-tag.

```bash
npm install -g @terminal-research/aion
aio
```

The workflow publishes GitHub prereleases to npm with the `next` dist-tag.

```bash
npm install -g @terminal-research/aion@next
aio
```

## One-Time npm Setup

Before automated publishing can work:

1. Create or confirm the npm organization scope `@terminal-research`.
2. If npm requires the package record to exist before trusted publishing can be
   configured, seed `@terminal-research/aion` once with a maintainer-owned
   manual publish.
3. In npm package settings for `@terminal-research/aion`, configure a trusted
   publisher for this GitHub repository and the `publish-aion.yml` workflow.
4. Keep the workflow's `id-token: write` permission enabled so GitHub Actions can
   mint the OIDC token during publish.

Trusted publishing removes the need for an `NPM_TOKEN` secret and enables npm
provenance. The workflow upgrades to `npm@^11.5.1` because current npm trusted
publishing requires npm 11.5.1 or newer.

## Manual Workflow Check

The workflow also supports `workflow_dispatch`. Running it manually does not
publish to npm; it installs dependencies, runs tests, builds the package, and
creates an npm tarball with `npm pack` for inspection.
