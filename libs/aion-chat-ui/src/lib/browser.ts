import { spawn, type SpawnOptions } from "node:child_process";

type SpawnImpl = typeof spawn;

export interface BrowserOpenCommand {
	command: string;
	args: string[];
	options: SpawnOptions;
}

function validateBrowserUrl(url: string): void {
	const parsed = new URL(url);
	if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
		throw new Error("Only HTTP and HTTPS login URLs can be opened in a browser.");
	}
}

export function buildBrowserOpenCommand(
	url: string,
	platform: NodeJS.Platform = process.platform
): BrowserOpenCommand {
	validateBrowserUrl(url);
	const options: SpawnOptions = {
		detached: true,
		stdio: "ignore"
	};

	switch (platform) {
		case "darwin":
			return {
				command: "open",
				args: [url],
				options
			};
		case "win32":
			return {
				command: "cmd",
				args: ["/c", "start", "", url],
				options: {
					...options,
					windowsHide: true
				}
			};
		default:
			return {
				command: "xdg-open",
				args: [url],
				options
			};
	}
}

export async function openUrlInDefaultBrowser(
	url: string,
	spawnImpl: SpawnImpl = spawn
): Promise<boolean> {
	const openCommand = buildBrowserOpenCommand(url);
	return new Promise((resolve) => {
		try {
			const child = spawnImpl(
				openCommand.command,
				openCommand.args,
				openCommand.options
			);
			child.once("spawn", () => {
				child.unref();
				resolve(true);
			});
			child.once("error", () => {
				resolve(false);
			});
		} catch {
			resolve(false);
		}
	});
}
