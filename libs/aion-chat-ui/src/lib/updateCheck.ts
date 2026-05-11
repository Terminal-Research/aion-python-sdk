import { spawn } from "node:child_process";
import { createInterface } from "node:readline/promises";

import { getPackageInfo } from "../packageInfo.js";

const DEFAULT_TIMEOUT_MS = 1500;

export type UpdateChoice = "global" | "local" | "skip" | "skip-version";

export interface PackageUpdate {
	packageName: string;
	currentVersion: string;
	latestVersion: string;
	releaseNotesUrl?: string;
}

export interface UpdateInstallCommand {
	command: string;
	args: string[];
}

interface NpmLatestResponse {
	version?: string;
}

export function buildNpmLatestVersionUrl(packageName: string): string {
	return `https://registry.npmjs.org/${packageName.replace("/", "%2F")}/latest`;
}

export function normalizeGitHubRepositoryUrl(
	repositoryUrl: string | undefined
): string | undefined {
	if (!repositoryUrl?.trim()) {
		return undefined;
	}

	const normalizedUrl = repositoryUrl
		.trim()
		.replace(/^git\+/u, "")
		.replace(/^git@github\.com:/u, "https://github.com/")
		.replace(/\.git$/u, "");

	try {
		const parsed = new URL(normalizedUrl);
		if (parsed.hostname !== "github.com") {
			return undefined;
		}
		const [owner, repository] = parsed.pathname
			.split("/")
			.filter((part) => part.length > 0);
		if (!owner || !repository) {
			return undefined;
		}
		return `https://github.com/${owner}/${repository}`;
	} catch {
		return undefined;
	}
}

export function buildGitHubReleaseNotesUrl(options: {
	repositoryUrl: string | undefined;
	version: string;
}): string | undefined {
	const normalizedRepositoryUrl = normalizeGitHubRepositoryUrl(
		options.repositoryUrl
	);
	if (!normalizedRepositoryUrl) {
		return undefined;
	}

	const tag = options.version.startsWith("v")
		? options.version
		: `v${options.version}`;
	return `${normalizedRepositoryUrl}/releases/tag/${encodeURIComponent(tag)}`;
}

function parseVersion(value: string): {
	major: number;
	minor: number;
	patch: number;
	prerelease?: string;
} | undefined {
	const match = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?/u.exec(value);
	if (!match) {
		return undefined;
	}
	return {
		major: Number(match[1]),
		minor: Number(match[2]),
		patch: Number(match[3]),
		...(match[4] ? { prerelease: match[4] } : {})
	};
}

export function comparePackageVersions(left: string, right: string): number {
	const parsedLeft = parseVersion(left);
	const parsedRight = parseVersion(right);
	if (!parsedLeft || !parsedRight) {
		return left.localeCompare(right);
	}

	for (const key of ["major", "minor", "patch"] as const) {
		const delta = parsedLeft[key] - parsedRight[key];
		if (delta !== 0) {
			return delta;
		}
	}

	if (parsedLeft.prerelease && !parsedRight.prerelease) {
		return -1;
	}
	if (!parsedLeft.prerelease && parsedRight.prerelease) {
		return 1;
	}
	return (parsedLeft.prerelease ?? "").localeCompare(parsedRight.prerelease ?? "");
}

export function shouldSkipUpdateCheck(
	env: NodeJS.ProcessEnv = process.env
): boolean {
	return (
		env.CI === "true" ||
		env.AION_CHAT_SKIP_UPDATE_CHECK === "1" ||
		env.AION_CHAT_UPDATE_CHECK === "0"
	);
}

export async function fetchLatestPackageVersion(options: {
	packageName: string;
	fetchImpl?: typeof fetch;
	timeoutMs?: number;
}): Promise<string | undefined> {
	const fetchImpl = options.fetchImpl ?? fetch;
	const controller = new AbortController();
	const timeout = setTimeout(() => {
		controller.abort();
	}, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);

	try {
		const response = await fetchImpl(buildNpmLatestVersionUrl(options.packageName), {
			headers: {
				Accept: "application/json"
			},
			signal: controller.signal
		});
		if (!response.ok) {
			return undefined;
		}

		const payload = (await response.json()) as NpmLatestResponse;
		return typeof payload.version === "string" && payload.version.trim()
			? payload.version.trim()
			: undefined;
	} catch {
		return undefined;
	} finally {
		clearTimeout(timeout);
	}
}

export async function detectPackageUpdate(options: {
	packageName?: string;
	currentVersion?: string;
	repositoryUrl?: string;
	skippedVersion?: string;
	fetchImpl?: typeof fetch;
	timeoutMs?: number;
	env?: NodeJS.ProcessEnv;
} = {}): Promise<PackageUpdate | undefined> {
	if (shouldSkipUpdateCheck(options.env)) {
		return undefined;
	}

	const packageInfo = getPackageInfo();
	const packageName = options.packageName ?? packageInfo.name;
	const currentVersion = options.currentVersion ?? packageInfo.version;
	const latestVersion = await fetchLatestPackageVersion({
		packageName,
		fetchImpl: options.fetchImpl,
		timeoutMs: options.timeoutMs
	});

	if (!latestVersion || comparePackageVersions(latestVersion, currentVersion) <= 0) {
		return undefined;
	}
	if (options.skippedVersion === latestVersion) {
		return undefined;
	}

	const releaseNotesUrl = buildGitHubReleaseNotesUrl({
		repositoryUrl: options.repositoryUrl ?? packageInfo.repositoryUrl,
		version: latestVersion
	});

	return {
		packageName,
		currentVersion,
		latestVersion,
		...(releaseNotesUrl ? { releaseNotesUrl } : {})
	};
}

export function getUpdateInstallCommand(
	choice: Exclude<UpdateChoice, "skip" | "skip-version">,
	packageName: string
): UpdateInstallCommand {
	return {
		command: process.platform === "win32" ? "npm.cmd" : "npm",
		args:
			choice === "global"
				? ["install", "-g", packageName]
				: ["install", packageName]
	};
}

export function formatInstallCommand(command: UpdateInstallCommand): string {
	return [command.command, ...command.args].join(" ");
}

export async function promptForUpdate(options: {
	update: PackageUpdate;
	input?: NodeJS.ReadStream;
	output?: NodeJS.WriteStream;
}): Promise<UpdateChoice> {
	const input = options.input ?? process.stdin;
	const output = options.output ?? process.stdout;
	const updateCommand = getUpdateInstallCommand(
		"global",
		options.update.packageName
	);
	const localCommand = getUpdateInstallCommand(
		"local",
		options.update.packageName
	);

	output.write(
		[
			"",
			`  ✨ Update available! ${options.update.currentVersion} -> ${options.update.latestVersion}`,
			"",
			...(options.update.releaseNotesUrl
				? [`  Release notes: ${options.update.releaseNotesUrl}`, ""]
				: []),
			`› 1. Update globally (runs \`${formatInstallCommand(updateCommand)}\`)`,
			`  2. Update in this project (runs \`${formatInstallCommand(localCommand)}\`)`,
			"  3. Skip",
			"  4. Skip until next version",
			"",
			"  Press enter to continue"
		].join("\n") + "\n"
	);

	const readline = createInterface({ input, output });
	try {
		while (true) {
			const answer = (await readline.question("")).trim();
			if (answer === "" || answer === "1") {
				return "global";
			}
			if (answer === "2") {
				return "local";
			}
			if (answer === "3") {
				return "skip";
			}
			if (answer === "4") {
				return "skip-version";
			}
			output.write("  Please choose 1, 2, 3, or 4.\n");
		}
	} finally {
		readline.close();
	}
}

export async function runUpdateInstall(
	command: UpdateInstallCommand,
	cwd = process.cwd()
): Promise<number> {
	return new Promise((resolve) => {
		const child = spawn(command.command, command.args, {
			cwd,
			stdio: "inherit"
		});
		child.on("error", () => {
			resolve(1);
		});
		child.on("close", (code) => {
			resolve(code ?? 1);
		});
	});
}
