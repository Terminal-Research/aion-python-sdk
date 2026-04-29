import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import type { Message } from "@a2a-js/sdk";
import { afterEach, describe, expect, it } from "vitest";

import {
	loadMostRecentSession,
	resolveSessionFilePath,
	saveCompletedExchange
} from "../src/lib/agents/sessionStore.js";

const tempDirectories: string[] = [];

afterEach(() => {
	for (const directory of tempDirectories.splice(0)) {
		rmSync(directory, { recursive: true, force: true });
	}
});

function buildMessage(messageId: string, role: "user" | "agent"): Message {
	return {
		kind: "message",
		messageId,
		role,
		parts: [{ kind: "text", text: `${role} text` }]
	};
}

describe("sessionStore", () => {
	it("stores completed exchanges in environment and agent-key scoped files", () => {
		const directory = mkdtempSync(path.join(os.tmpdir(), "chat2-sessions-"));
		tempDirectories.push(directory);
		const sessionsDirectory = path.join(directory, "sessions");

		expect(
			saveCompletedExchange(
				{
					environment: "development",
					agentKey: "default-localhost-8000:command-agent",
					contextId: "ctx_123",
					lastTaskId: "task_1",
					messages: [buildMessage("user-1", "user"), buildMessage("agent-1", "agent")]
				},
				sessionsDirectory
			)
		).toBeUndefined();

		const filePath = resolveSessionFilePath(
			"development",
			"default-localhost-8000:command-agent",
			"ctx_123",
			sessionsDirectory
		);
		const stored = JSON.parse(readFileSync(filePath, "utf8"));
		expect(stored).toMatchObject({
			schemaVersion: 1,
			environment: "development",
			agentKey: "default-localhost-8000:command-agent",
			contextId: "ctx_123",
			localTurnCount: 1,
			lastTaskId: "task_1"
		});
		expect(stored.messages).toHaveLength(2);
	});

	it("loads the most recently updated session for an agent", () => {
		const directory = mkdtempSync(path.join(os.tmpdir(), "chat2-sessions-"));
		tempDirectories.push(directory);
		const sessionsDirectory = path.join(directory, "sessions");

		saveCompletedExchange(
			{
				environment: "production",
				agentKey: "agent",
				contextId: "ctx_old",
				messages: [buildMessage("old", "user")]
			},
			sessionsDirectory
		);
		saveCompletedExchange(
			{
				environment: "production",
				agentKey: "agent",
				contextId: "ctx_new",
				messages: [buildMessage("new", "user")]
			},
			sessionsDirectory
		);

		expect(
			loadMostRecentSession("production", "agent", sessionsDirectory)?.contextId
		).toBe("ctx_new");
	});
});
