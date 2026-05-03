import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
	loadChatSettings,
	loadChatModeSettings,
	resolveChatSettingsPath,
	saveChatSettings,
	saveChatModeSettings
} from "../src/lib/chatSettings.js";
import {
	createDefaultLocalAgentSource,
	createDefaultRegistryAgentSource
} from "../src/lib/agents/model.js";
import type { AionEnvironmentId } from "../src/lib/environment.js";

const tempDirectories: string[] = [];

function defaultWorkspace(environmentId: AionEnvironmentId) {
	const defaultSource = createDefaultLocalAgentSource();
	const registrySource = createDefaultRegistryAgentSource(environmentId);
	return {
		agentSources: {
			[defaultSource.sourceKey]: defaultSource,
			[registrySource.sourceKey]: registrySource
		},
		agents: {}
	};
}

afterEach(() => {
	for (const directory of tempDirectories.splice(0)) {
		rmSync(directory, { recursive: true, force: true });
	}
});

describe("chatSettings", () => {
	it("resolves settings into the XDG config home", () => {
		expect(
			resolveChatSettingsPath({ XDG_CONFIG_HOME: "/tmp/xdg" }, "/tmp/home")
		).toBe("/tmp/xdg/aion/chat2.json");
	});

	it("saves and reloads persisted modes", () => {
		const directory = mkdtempSync(path.join(os.tmpdir(), "chat2-settings-"));
		tempDirectories.push(directory);
		const settingsPath = path.join(directory, "aion", "chat2.json");

		expect(
			saveChatModeSettings(
				{
					requestMode: "streaming-message",
					responseMode: "a2a-protocol"
				},
				settingsPath
			)
		).toBeUndefined();

		expect(loadChatModeSettings(settingsPath)).toEqual({
			settings: {
				requestMode: "streaming-message",
				responseMode: "a2a-protocol"
			}
		});
	});

	it("stores selected environment and per-environment settings in one file", () => {
		const directory = mkdtempSync(path.join(os.tmpdir(), "chat2-settings-"));
		tempDirectories.push(directory);
		const settingsPath = path.join(directory, "aion", "chat2.json");

		expect(
			saveChatSettings(
				{
					selectedEnvironment: "development",
					environments: {
						production: {
							requestMode: "send-message",
							responseMode: "message-output",
							...defaultWorkspace("production")
						},
						staging: {
							requestMode: "send-message",
							responseMode: "message-output",
							...defaultWorkspace("staging")
						},
						development: {
							requestMode: "streaming-message",
							responseMode: "a2a-protocol",
							selectedAgentId: "dev-agent",
							...defaultWorkspace("development")
						}
					}
				},
				settingsPath
			)
		).toBeUndefined();

		expect(loadChatSettings(settingsPath)).toEqual({
			settings: {
				selectedEnvironment: "development",
				environments: {
					production: {
						requestMode: "send-message",
						responseMode: "message-output",
						...defaultWorkspace("production")
					},
					staging: {
						requestMode: "send-message",
						responseMode: "message-output",
						...defaultWorkspace("staging")
					},
					development: {
						requestMode: "streaming-message",
						responseMode: "a2a-protocol",
						selectedAgentId: "dev-agent",
						...defaultWorkspace("development")
					}
				}
			}
		});
	});

	it("migrates legacy flat mode settings into the selected environment", () => {
		const directory = mkdtempSync(path.join(os.tmpdir(), "chat2-settings-"));
		tempDirectories.push(directory);
		const settingsPath = path.join(directory, "aion", "chat2.json");
		mkdirSync(path.dirname(settingsPath), { recursive: true });
		writeFileSync(
			settingsPath,
			`${JSON.stringify({
				requestMode: "streaming-message",
				responseMode: "a2a-protocol"
			})}\n`,
			"utf8"
		);

		expect(loadChatSettings(settingsPath).settings.environments.production).toEqual({
			requestMode: "streaming-message",
			responseMode: "a2a-protocol",
			...defaultWorkspace("production")
		});
	});

	it("falls back to defaults when the settings file is missing", () => {
		const directory = mkdtempSync(path.join(os.tmpdir(), "chat2-settings-"));
		tempDirectories.push(directory);

		expect(loadChatModeSettings(path.join(directory, "missing.json"))).toEqual({
			settings: {
				requestMode: "send-message",
				responseMode: "message-output"
			}
		});
	});
});
