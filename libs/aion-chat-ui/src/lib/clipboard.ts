import { spawn, type SpawnOptions } from "node:child_process";

type SpawnImpl = typeof spawn;

export interface ClipboardWriteCommand {
	command: string;
	args: string[];
	options?: SpawnOptions;
}

export function buildClipboardWriteCommands(
	platform: NodeJS.Platform = process.platform,
	env: NodeJS.ProcessEnv = process.env
): ClipboardWriteCommand[] {
	if (platform === "darwin") {
		return [{ command: "pbcopy", args: [] }];
	}

	if (platform === "win32") {
		return [
			{ command: "clip", args: [], options: { windowsHide: true } }
		];
	}

	const linuxCommands: ClipboardWriteCommand[] = [
		{ command: "xclip", args: ["-selection", "clipboard"] },
		{ command: "xsel", args: ["--clipboard", "--input"] }
	];

	if (env.WAYLAND_DISPLAY) {
		return [{ command: "wl-copy", args: [] }, ...linuxCommands];
	}

	return [...linuxCommands, { command: "wl-copy", args: [] }];
}

async function runClipboardCommand(
	clipboardCommand: ClipboardWriteCommand,
	content: string,
	spawnImpl: SpawnImpl
): Promise<boolean> {
	return new Promise((resolve) => {
		let settled = false;
		const settle = (result: boolean): void => {
			if (settled) {
				return;
			}
			settled = true;
			resolve(result);
		};

		try {
			const child = spawnImpl(clipboardCommand.command, clipboardCommand.args, {
				...clipboardCommand.options,
				stdio: ["pipe", "ignore", "ignore"]
			});
			child.once("error", () => settle(false));
			child.once("exit", (code) => settle(code === 0));
			if (!child.stdin) {
				settle(false);
				return;
			}
			child.stdin.once("error", () => undefined);
			child.stdin.end(content);
		} catch {
			settle(false);
		}
	});
}

export async function writeClipboard(
	content: string,
	options: {
		commands?: ClipboardWriteCommand[];
		spawnImpl?: SpawnImpl;
	} = {}
): Promise<void> {
	const commands = options.commands ?? buildClipboardWriteCommands();
	const spawnImpl = options.spawnImpl ?? spawn;

	for (const command of commands) {
		if (await runClipboardCommand(command, content, spawnImpl)) {
			return;
		}
	}

	throw new Error("No supported clipboard command is available.");
}
