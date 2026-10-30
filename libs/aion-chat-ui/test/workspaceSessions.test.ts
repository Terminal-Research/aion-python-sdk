import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { Message, Role } from "@a2a-js/sdk";
import { copyContextSession, loadContextSession, resolveSessionFilePath, saveCompletedExchange } from "../src/lib/agents/sessionStore.js";
import { restoredTranscriptEntries } from "../src/lib/restoredTranscript.js";
import { WELCOME_MESSAGE_EXTENSION_URI } from "../src/lib/welcomeMessage.js";

const directories: string[] = [];
afterEach(() => directories.splice(0).forEach((directory) => rmSync(directory, { recursive: true, force: true })));
function fixture() {
	const directory = mkdtempSync(path.join(os.tmpdir(), "workspace-history-"));
	directories.push(directory);
	const messages = [
		Message.fromJSON({ messageId: "welcome-request", role: Role.ROLE_USER, contextId: "active", extensions: [WELCOME_MESSAGE_EXTENSION_URI], parts: [] }),
		Message.fromJSON({ messageId: "user", role: Role.ROLE_USER, contextId: "active", parts: [{ text: "Earlier question" }] }),
		Message.fromJSON({ messageId: "answer", role: Role.ROLE_AGENT, contextId: "active", parts: [{ text: "Earlier answer" }] })
	];
	saveCompletedExchange({ environment: "production", agentKey: "workspace-A", contextId: "active", messages, lastTaskId: "completed-task" }, directory);
	saveCompletedExchange({ environment: "production", agentKey: "workspace-A", contextId: "newer", messages: [] }, directory);
	saveCompletedExchange({ environment: "production", agentKey: "workspace-B", contextId: "active", messages: [] }, directory);
	return { directory, file: resolveSessionFilePath("production", "workspace-A", "active", directory), messages };
}
describe("exact workspace transcript restoration", () => {
	it("loads the active context instead of a newer file, renders once and does not record another turn", () => {
		const { directory, file, messages } = fixture();
		const raw = JSON.parse(readFileSync(file, "utf8"));
		raw.messages.push(messages[1]);
		writeFileSync(file, JSON.stringify(raw));
		const before = readFileSync(file, "utf8");
		const result = loadContextSession("production", "workspace-A", "active", directory);
		expect(result.warning).toBeUndefined();
		expect(result.session?.messages.map((message) => message.messageId)).toEqual(["welcome-request", "user", "answer"]);
		expect(restoredTranscriptEntries(result.session!.messages).map(({ role, body }) => ({ role, body }))).toEqual([
			{ role: "user", body: "Earlier question" }, { role: "agent", body: "Earlier answer" }
		]);
		expect(result.session?.localTurnCount).toBe(1);
		expect(readFileSync(file, "utf8")).toBe(before);
		expect(loadContextSession("production", "workspace-B", "active", directory).session?.messages).toEqual([]);
	});
	it.each(["missing", "invalid JSON", "wrong workspace", "wrong context", "malformed message", "malformed raw", "malformed extensions", "malformed task"])("reports %s without another session fallback", (kind) => {
		const { directory, file } = fixture();
		const raw = JSON.parse(readFileSync(file, "utf8"));
		if (kind === "wrong workspace") raw.agentKey = "workspace-B";
		if (kind === "wrong context") raw.contextId = "different";
		if (kind === "malformed message") raw.messages[1].parts = "bad";
		if (kind === "malformed raw") raw.messages[1].parts = [{ content: { $case: "raw" } }];
		if (kind === "malformed task") raw.messages[1].taskId = 10;
		if (kind === "malformed extensions") raw.messages[1].extensions = {};
		writeFileSync(file, kind === "invalid JSON" ? "{" : JSON.stringify(raw));
		if (kind === "missing") rmSync(file);
		const result = loadContextSession("production", "workspace-A", "active", directory);
		expect(result.session).toBeUndefined();
		expect(result.warning).toContain("still selected");
	});
	it("copies verified legacy history without changing its original or turn count", () => {
		const { directory, file } = fixture();
		const before = readFileSync(file, "utf8");
		const session = loadContextSession("production", "workspace-A", "active", directory).session!;
		expect(copyContextSession(session, "scoped-home", directory)).toBeUndefined();
		expect(loadContextSession("production", "scoped-home", "active", directory).session).toEqual({ ...session, agentKey: "scoped-home" });
		expect(readFileSync(file, "utf8")).toBe(before);
	});
});
