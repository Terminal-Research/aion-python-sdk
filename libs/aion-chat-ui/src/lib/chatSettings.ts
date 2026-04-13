import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import {
	type ChatModeSettings,
	DEFAULT_CHAT_MODE_SETTINGS
} from "./slashCommands.js";

interface ChatSettingsFile {
	requestMode?: string;
	responseMode?: string;
}

export interface ChatSettingsLoadResult {
	settings: ChatModeSettings;
	warning?: string;
}

function isRequestMode(value: string | undefined): value is ChatModeSettings["requestMode"] {
	return value === "send-message" || value === "streaming-message";
}

function isResponseMode(value: string | undefined): value is ChatModeSettings["responseMode"] {
	return value === "message-output" || value === "a2a-protocol";
}

export function resolveChatSettingsPath(
	env: NodeJS.ProcessEnv = process.env,
	homeDirectory = os.homedir()
): string {
	const configHome = env.XDG_CONFIG_HOME || path.join(homeDirectory, ".config");
	return path.join(configHome, "aion", "chat2.json");
}

export function loadChatModeSettings(settingsPath = resolveChatSettingsPath()): ChatSettingsLoadResult {
	try {
		const raw = readFileSync(settingsPath, "utf8");
		const parsed = JSON.parse(raw) as ChatSettingsFile;

		const requestMode = isRequestMode(parsed.requestMode)
			? parsed.requestMode
			: DEFAULT_CHAT_MODE_SETTINGS.requestMode;
		const responseMode = isResponseMode(parsed.responseMode)
			? parsed.responseMode
			: DEFAULT_CHAT_MODE_SETTINGS.responseMode;

		if (
			requestMode !== (parsed.requestMode as ChatSettingsFile["requestMode"]) ||
			responseMode !== (parsed.responseMode as ChatSettingsFile["responseMode"])
		) {
			return {
				settings: { requestMode, responseMode },
				warning: "chat2 ignored invalid saved mode settings and restored defaults."
			};
		}

		return {
			settings: { requestMode, responseMode }
		};
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		if (message.includes("ENOENT")) {
			return {
				settings: { ...DEFAULT_CHAT_MODE_SETTINGS }
			};
		}

		return {
			settings: { ...DEFAULT_CHAT_MODE_SETTINGS },
			warning: `chat2 could not load saved mode settings: ${message}`
		};
	}
}

export function saveChatModeSettings(
	settings: ChatModeSettings,
	settingsPath = resolveChatSettingsPath()
): string | undefined {
	try {
		mkdirSync(path.dirname(settingsPath), { recursive: true });
		writeFileSync(`${settingsPath}`, `${JSON.stringify(settings, null, 2)}\n`, "utf8");
		return undefined;
	} catch (error) {
		return `chat2 could not save mode settings: ${
			error instanceof Error ? error.message : String(error)
		}`;
	}
}
