import { PassThrough, Writable } from "node:stream";

import { describe, expect, it, vi } from "vitest";

import {
	buildGitHubReleaseNotesUrl,
	buildNpmLatestVersionUrl,
	comparePackageVersions,
	detectPackageUpdate,
	fetchLatestPackageVersion,
	formatInstallCommand,
	getUpdateInstallCommand,
	promptForUpdate,
	shouldSkipUpdateCheck
} from "../src/lib/updateCheck.js";

function createWritableBuffer(): {
	output: NodeJS.WriteStream;
	getValue: () => string;
} {
	let value = "";
	const output = new Writable({
		write(chunk, _encoding, callback) {
			value += chunk.toString();
			callback();
		}
	});
	return {
		output: output as NodeJS.WriteStream,
		getValue: () => value
	};
}

describe("updateCheck", () => {
	it("builds npm latest-version URLs for scoped packages", () => {
		expect(buildNpmLatestVersionUrl("@terminal-research/aion")).toBe(
			"https://registry.npmjs.org/@terminal-research%2Faion/latest"
		);
	});

	it("builds GitHub release-note URLs for version tags", () => {
		expect(
			buildGitHubReleaseNotesUrl({
				repositoryUrl:
					"git+https://github.com/Terminal-Research/aion-python-sdk.git",
				version: "0.2.0"
			})
		).toBe(
			"https://github.com/Terminal-Research/aion-python-sdk/releases/tag/v0.2.0"
		);
	});

	it("compares semver versions", () => {
		expect(comparePackageVersions("0.1.3", "0.1.2")).toBeGreaterThan(0);
		expect(comparePackageVersions("1.0.0", "0.9.9")).toBeGreaterThan(0);
		expect(comparePackageVersions("1.0.0-beta.1", "1.0.0")).toBeLessThan(0);
		expect(comparePackageVersions("1.0.0", "1.0.0")).toBe(0);
	});

	it("detects when npm has a newer latest version", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify({ version: "0.2.0" }), { status: 200 })
		) as unknown as typeof fetch;

		await expect(
			detectPackageUpdate({
				packageName: "@terminal-research/aion",
				currentVersion: "0.1.2",
				repositoryUrl:
					"git+https://github.com/Terminal-Research/aion-python-sdk.git",
				fetchImpl,
				env: {}
			})
		).resolves.toEqual({
			packageName: "@terminal-research/aion",
			currentVersion: "0.1.2",
			latestVersion: "0.2.0",
			releaseNotesUrl:
				"https://github.com/Terminal-Research/aion-python-sdk/releases/tag/v0.2.0"
		});
	});

	it("ignores a latest version skipped until the next release", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify({ version: "0.2.0" }), { status: 200 })
		) as unknown as typeof fetch;

		await expect(
			detectPackageUpdate({
				packageName: "@terminal-research/aion",
				currentVersion: "0.1.2",
				skippedVersion: "0.2.0",
				fetchImpl,
				env: {}
			})
		).resolves.toBeUndefined();
	});

	it("ignores missing or current latest versions", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify({ version: "0.1.2" }), { status: 200 })
		) as unknown as typeof fetch;

		await expect(
			detectPackageUpdate({
				packageName: "@terminal-research/aion",
				currentVersion: "0.1.2",
				fetchImpl,
				env: {}
			})
		).resolves.toBeUndefined();
	});

	it("fetches latest version payloads safely", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify({ version: "0.2.0" }), { status: 200 })
		) as unknown as typeof fetch;

		await expect(
			fetchLatestPackageVersion({
				packageName: "@terminal-research/aion",
				fetchImpl
			})
		).resolves.toBe("0.2.0");
		expect(fetchImpl).toHaveBeenCalledWith(
			"https://registry.npmjs.org/@terminal-research%2Faion/latest",
			expect.objectContaining({
				headers: {
					Accept: "application/json"
				}
			})
		);
	});

	it("supports update-check skip environment variables", () => {
		expect(shouldSkipUpdateCheck({ CI: "true" })).toBe(true);
		expect(shouldSkipUpdateCheck({ AION_CHAT_SKIP_UPDATE_CHECK: "1" })).toBe(
			true
		);
		expect(shouldSkipUpdateCheck({ AION_CHAT_UPDATE_CHECK: "0" })).toBe(true);
		expect(shouldSkipUpdateCheck({})).toBe(false);
	});

	it("builds global and local install commands", () => {
		const globalCommand = getUpdateInstallCommand(
			"global",
			"@terminal-research/aion"
		);
		const localCommand = getUpdateInstallCommand(
			"local",
			"@terminal-research/aion"
		);

		expect(globalCommand.args).toEqual([
			"install",
			"-g",
			"@terminal-research/aion"
		]);
		expect(localCommand.args).toEqual(["install", "@terminal-research/aion"]);
		expect(formatInstallCommand(globalCommand)).toContain(
			"install -g @terminal-research/aion"
		);
	});

	it("renders the update prompt with release notes and defaults enter to update", async () => {
		const input = new PassThrough();
		const { output, getValue } = createWritableBuffer();
		input.end("\n");

		await expect(
			promptForUpdate({
				update: {
					packageName: "@terminal-research/aion",
					currentVersion: "0.1.2",
					latestVersion: "0.2.0",
					releaseNotesUrl:
						"https://github.com/Terminal-Research/aion-python-sdk/releases/tag/v0.2.0"
				},
				input: input as unknown as NodeJS.ReadStream,
				output
			})
		).resolves.toBe("global");

		expect(getValue()).toContain("✨ Update available! 0.1.2 -> 0.2.0");
		expect(getValue()).toContain(
			"Release notes: https://github.com/Terminal-Research/aion-python-sdk/releases/tag/v0.2.0"
		);
		expect(getValue()).toContain(
			"› 1. Update globally (runs `npm install -g @terminal-research/aion`)"
		);
		expect(getValue()).toContain(
			"2. Update in this project (runs `npm install @terminal-research/aion`)"
		);
		expect(getValue()).toContain("4. Skip until next version");
	});

	it("returns local update and skip choices from the update prompt", async () => {
		const localInput = new PassThrough();
		const skipVersionInput = new PassThrough();
		const localOutput = createWritableBuffer();
		const skipVersionOutput = createWritableBuffer();
		localInput.end("2\n");
		skipVersionInput.end("4\n");

		await expect(
			promptForUpdate({
				update: {
					packageName: "@terminal-research/aion",
					currentVersion: "0.1.2",
					latestVersion: "0.2.0"
				},
				input: localInput as unknown as NodeJS.ReadStream,
				output: localOutput.output
			})
		).resolves.toBe("local");

		await expect(
			promptForUpdate({
				update: {
					packageName: "@terminal-research/aion",
					currentVersion: "0.1.2",
					latestVersion: "0.2.0"
				},
				input: skipVersionInput as unknown as NodeJS.ReadStream,
				output: skipVersionOutput.output
			})
		).resolves.toBe("skip-version");
	});
});
