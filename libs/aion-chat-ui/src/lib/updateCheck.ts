import { spawn } from "node:child_process";
import { createInterface } from "node:readline/promises";

import { getPackageInfo } from "../packageInfo.js";

const DEFAULT_TIMEOUT_MS = 1500;

export type UpdateChoice = "global" | "local" | "skip";

export interface PackageUpdate {
	packageName: string;
	currentVersion: string;
	latestVersion: string;
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

	return {
		packageName,
		currentVersion,
		latestVersion
	};
}

export function getUpdateInstallCommand(
	choice: Exclude<UpdateChoice, "skip">,
	packageName: string
): UpdateInstallCommand {
	const packageTarget = `${packageName}@latest`;
	return {
		command: process.platform === "win32" ? "npm.cmd" : "npm",
		args:
			choice === "global"
				? ["install", "-g", packageTarget]
				: ["install", packageTarget]
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
	const globalCommand = getUpdateInstallCommand("global", options.update.packageName);
	const localCommand = getUpdateInstallCommand("local", options.update.packageName);

	output.write(
		[
			"",
			`A new ${options.update.packageName} version is available: ${options.update.currentVersion} -> ${options.update.latestVersion}`,
			`1. Install globally: ${formatInstallCommand(globalCommand)}`,
			`2. Install in this project: ${formatInstallCommand(localCommand)}`,
			"3. Skip for now",
			""
		].join("\n")
	);

	const readline = createInterface({ input, output });
	try {
		while (true) {
			const answer = (await readline.question("Choose 1, 2, or 3: ")).trim();
			if (answer === "1") {
				return "global";
			}
			if (answer === "2") {
				return "local";
			}
			if (answer === "3" || answer === "") {
				return "skip";
			}
			output.write("Please choose 1, 2, or 3.\n");
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
