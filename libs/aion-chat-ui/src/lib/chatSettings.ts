import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import {
	type AionEnvironmentId,
	AION_ENVIRONMENT_IDS,
	DEFAULT_AION_ENVIRONMENT_ID,
	isAionEnvironmentId
} from "./environment.js";
import {
	type ChatModeSettings,
	DEFAULT_CHAT_MODE_SETTINGS
} from "./slashCommands.js";

interface ChatEnvironmentSettingsFile {
	requestMode?: string;
	responseMode?: string;
	selectedAgentId?: string | null;
}

interface ChatSettingsFile {
	selectedEnvironment?: string;
	environments?: Partial<Record<AionEnvironmentId, ChatEnvironmentSettingsFile>>;
	requestMode?: string;
	responseMode?: string;
	[key: string]: unknown;
}

export interface ChatEnvironmentSettings extends ChatModeSettings {
	selectedAgentId?: string;
}

export interface ChatSettings {
	selectedEnvironment: AionEnvironmentId;
	environments: Record<AionEnvironmentId, ChatEnvironmentSettings>;
}

export interface ChatSettingsLoadResult {
	settings: ChatSettings;
	warning?: string;
}

export interface ChatModeSettingsLoadResult {
	settings: ChatModeSettings;
	warning?: string;
}

function isRequestMode(value: string | undefined): value is ChatModeSettings["requestMode"] {
	return value === "send-message" || value === "streaming-message";
}

function isResponseMode(value: string | undefined): value is ChatModeSettings["responseMode"] {
	return value === "message-output" || value === "a2a-protocol";
}

function defaultEnvironmentSettings(): ChatEnvironmentSettings {
	return {
		...DEFAULT_CHAT_MODE_SETTINGS
	};
}

function defaultSettings(): ChatSettings {
	return {
		selectedEnvironment: DEFAULT_AION_ENVIRONMENT_ID,
		environments: Object.fromEntries(
			AION_ENVIRONMENT_IDS.map((id) => [id, defaultEnvironmentSettings()])
		) as Record<AionEnvironmentId, ChatEnvironmentSettings>
	};
}

function normalizeEnvironmentSettings(
	value: ChatEnvironmentSettingsFile | undefined,
	fallback: ChatEnvironmentSettings
): ChatEnvironmentSettings {
	const requestMode = isRequestMode(value?.requestMode)
		? value.requestMode
		: fallback.requestMode;
	const responseMode = isResponseMode(value?.responseMode)
		? value.responseMode
		: fallback.responseMode;
	const selectedAgentId =
		typeof value?.selectedAgentId === "string" && value.selectedAgentId.trim()
			? value.selectedAgentId
			: undefined;

	return {
		requestMode,
		responseMode,
		...(selectedAgentId ? { selectedAgentId } : {})
	};
}

function normalizeSettings(parsed: ChatSettingsFile): {
	settings: ChatSettings;
	warning?: string;
} {
	const defaults = defaultSettings();
	const selectedEnvironment = isAionEnvironmentId(parsed.selectedEnvironment)
		? parsed.selectedEnvironment
		: DEFAULT_AION_ENVIRONMENT_ID;
	const legacyFallback = normalizeEnvironmentSettings(
		{
			requestMode: parsed.requestMode,
			responseMode: parsed.responseMode
		},
		defaults.environments[selectedEnvironment]
	);
	const environments = Object.fromEntries(
		AION_ENVIRONMENT_IDS.map((id) => [
			id,
			normalizeEnvironmentSettings(
				parsed.environments?.[id],
				id === selectedEnvironment ? legacyFallback : defaults.environments[id]
			)
		])
	) as Record<AionEnvironmentId, ChatEnvironmentSettings>;
	const hadInvalidEnvironment =
		parsed.selectedEnvironment !== undefined &&
		!isAionEnvironmentId(parsed.selectedEnvironment);

	return {
		settings: {
			selectedEnvironment,
			environments
		},
		...(hadInvalidEnvironment
			? {
					warning:
						"chat2 ignored an invalid saved environment and restored production."
				}
			: {})
	};
}

function readRawSettings(settingsPath: string): ChatSettingsFile | undefined {
	try {
		return JSON.parse(readFileSync(settingsPath, "utf8")) as ChatSettingsFile;
	} catch {
		return undefined;
	}
}

function serializeSettings(
	settings: ChatSettings,
	rawSettings: ChatSettingsFile | undefined
): ChatSettingsFile {
	const {
		selectedEnvironment: _selectedEnvironment,
		environments: _environments,
		requestMode: _requestMode,
		responseMode: _responseMode,
		...unknownFields
	} = rawSettings ?? {};

	return {
		...unknownFields,
		selectedEnvironment: settings.selectedEnvironment,
		environments: settings.environments
	};
}

export function resolveChatSettingsPath(
	env: NodeJS.ProcessEnv = process.env,
	homeDirectory = os.homedir()
): string {
	const configHome = env.XDG_CONFIG_HOME || path.join(homeDirectory, ".config");
	return path.join(configHome, "aion", "chat2.json");
}

export function loadChatSettings(
	settingsPath = resolveChatSettingsPath()
): ChatSettingsLoadResult {
	try {
		const raw = readFileSync(settingsPath, "utf8");
		const parsed = JSON.parse(raw) as ChatSettingsFile;
		return normalizeSettings(parsed);
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		if (message.includes("ENOENT")) {
			return {
				settings: defaultSettings()
			};
		}

		return {
			settings: defaultSettings(),
			warning: `chat2 could not load saved settings: ${message}`
		};
	}
}

export function saveChatSettings(
	settings: ChatSettings,
	settingsPath = resolveChatSettingsPath()
): string | undefined {
	try {
		const rawSettings = readRawSettings(settingsPath);
		const serialized = serializeSettings(settings, rawSettings);
		mkdirSync(path.dirname(settingsPath), { recursive: true });
		writeFileSync(
			settingsPath,
			`${JSON.stringify(serialized, null, 2)}\n`,
			"utf8"
		);
		return undefined;
	} catch (error) {
		return `chat2 could not save settings: ${
			error instanceof Error ? error.message : String(error)
		}`;
	}
}

export function updateChatSettings(
	update: (settings: ChatSettings) => ChatSettings,
	settingsPath = resolveChatSettingsPath()
): string | undefined {
	const { settings } = loadChatSettings(settingsPath);
	return saveChatSettings(update(settings), settingsPath);
}

export function loadChatModeSettings(
	settingsPath = resolveChatSettingsPath()
): ChatModeSettingsLoadResult {
	const { settings, warning } = loadChatSettings(settingsPath);
	const activeSettings = settings.environments[settings.selectedEnvironment];
	return {
		settings: {
			requestMode: activeSettings.requestMode,
			responseMode: activeSettings.responseMode
		},
		...(warning ? { warning } : {})
	};
}

export function saveChatModeSettings(
	nextSettings: ChatModeSettings,
	settingsPath = resolveChatSettingsPath()
): string | undefined {
	return updateChatSettings((settings) => {
		const current = settings.environments[settings.selectedEnvironment];
		return {
			...settings,
			environments: {
				...settings.environments,
				[settings.selectedEnvironment]: {
					...current,
					requestMode: nextSettings.requestMode,
					responseMode: nextSettings.responseMode
				}
			}
		};
	}, settingsPath);
}

export function saveSelectedEnvironment(
	environmentId: AionEnvironmentId,
	settingsPath = resolveChatSettingsPath()
): string | undefined {
	return updateChatSettings((settings) => ({
		...settings,
		selectedEnvironment: environmentId
	}), settingsPath);
}

export function saveSelectedAgent(
	environmentId: AionEnvironmentId,
	selectedAgentId: string | undefined,
	settingsPath = resolveChatSettingsPath()
): string | undefined {
	return updateChatSettings((settings) => {
		const current = settings.environments[environmentId];
		const nextEnvironmentSettings: ChatEnvironmentSettings = {
			...current,
			...(selectedAgentId ? { selectedAgentId } : {})
		};
		if (!selectedAgentId) {
			delete nextEnvironmentSettings.selectedAgentId;
		}

		return {
			...settings,
			environments: {
				...settings.environments,
				[environmentId]: nextEnvironmentSettings
			}
		};
	}, settingsPath);
}
