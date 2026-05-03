import { describe, expect, it, vi } from "vitest";

import {
	buildNpmLatestVersionUrl,
	comparePackageVersions,
	detectPackageUpdate,
	fetchLatestPackageVersion,
	formatInstallCommand,
	getUpdateInstallCommand,
	shouldSkipUpdateCheck
} from "../src/lib/updateCheck.js";

describe("updateCheck", () => {
	it("builds npm latest-version URLs for scoped packages", () => {
		expect(buildNpmLatestVersionUrl("@terminal-research/aion")).toBe(
			"https://registry.npmjs.org/@terminal-research%2Faion/latest"
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
				fetchImpl,
				env: {}
			})
		).resolves.toEqual({
			packageName: "@terminal-research/aion",
			currentVersion: "0.1.2",
			latestVersion: "0.2.0"
		});
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
			"@terminal-research/aion@latest"
		]);
		expect(localCommand.args).toEqual([
			"install",
			"@terminal-research/aion@latest"
		]);
		expect(formatInstallCommand(globalCommand)).toContain(
			"install -g @terminal-research/aion@latest"
		);
	});
});
