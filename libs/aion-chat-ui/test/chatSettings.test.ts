import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
	loadChatModeSettings,
	resolveChatSettingsPath,
	saveChatModeSettings
} from "../src/lib/chatSettings.js";

const tempDirectories: string[] = [];

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
