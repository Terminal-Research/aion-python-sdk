import React from "react";
import { render } from "ink-testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentCard } from "@a2a-js/sdk";
import { ChatApp } from "../src/app.js";
import { runHeadless } from "../src/lib/headlessRun.js";
import { anonymousSessionKey } from "../src/lib/anonymousSession.js";
import { createAgentKey, createDefaultLocalAgentSource } from "../src/lib/agents/model.js";

const state = vi.hoisted(() => ({
	values: new Map<string, string>(), logs: [] as unknown[],
	settings: {} as import("../src/lib/chatSettings.js").ChatSettings
}));
vi.mock("../src/lib/credentialStore.js", () => ({
	defaultCredentialStore: {
		getAnonymousSession: async (key: string) => state.values.get(key),
		setAnonymousSession: async (key: string, value: string) => { state.values.set(key, value); }
	}
}));
vi.mock("../src/lib/workosAuth.js", () => ({
	getStoredAccessToken: async () => undefined,
	loginWithWorkOS: vi.fn()
}));
vi.mock("../src/lib/chatSettings.js", async (original) => ({
	...await original<typeof import("../src/lib/chatSettings.js")>(),
	loadChatSettings: () => ({ settings: state.settings }),
	saveChatSettings: (settings: typeof state.settings) => { state.settings = settings; }
}));
vi.mock("../src/lib/agents/sessionStore.js", () => ({
	resolveSessionFilePath: () => "/test-only/session.json",
	saveCompletedExchange: vi.fn(), loadMostRecentSession: () => undefined
}));
vi.mock("../src/lib/sessionLogger.js", () => {
	const log = (...args: unknown[]) => { state.logs.push(args); };
	return { createChatSessionLogger: () => ({ logger: {
		chatSessionId: "test", logFilePath: "/test-only/log.jsonl",
		debug: log, info: log, warn: log, error: log, flush: vi.fn()
	} }) };
});

const local = createDefaultLocalAgentSource();
const agentKey = createAgentKey(local.sourceKey, "demo");
const sessionKey = anonymousSessionKey({ controlPlaneUrl: "http://localhost:8080", environmentId: "development", sourceId: local.sourceKey, destinationOrigin: local.url });
const options = { agentId: "demo", headers: {}, pushNotifications: false, pushReceiver: "http://localhost:5000" };
let calls: Array<{ url: string; authorization: string | null; body?: Record<string, any> }>;
let sequence: number;
let app: ReturnType<typeof render> | undefined;

beforeEach(() => {
	state.values.clear(); state.logs = []; calls = []; sequence = 0;
	const environment = { requestMode: "send-message" as const, responseMode: "message-output" as const, agentSources: {}, agents: {} };
	state.settings = { selectedEnvironment: "development", environments: {
		production: { ...environment }, staging: { ...environment }, development: {
			...environment, selectedAgentId: "demo", selectedAgentKey: agentKey,
			agents: { [agentKey]: { agentKey, sourceKey: local.sourceKey, agentId: "demo", agentCardUrl: local.url + "/agents/demo/.well-known/agent-card.json", lastSeenAt: new Date().toISOString(), activeContextId: "someone-elses-context", credentialScope: "guest:other" } }
		}
	} };
	vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
		const request = new Request(input, init);
		const body = request.method === "POST" && request.headers.has("Content-Type") ? await request.json() : undefined;
		calls.push({ url: request.url, authorization: request.headers.get("Authorization"), body });
		if (request.url === "http://localhost:8080/auth/anonymous-sessions") {
			sequence++;
			return Response.json({ sessionId: `00000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`, token: `secret-guest-${sequence}`, expiresAt: new Date(Date.now() + 30 * 86400000).toISOString() });
		}
		if (!request.url.startsWith(local.url)) throw new Error("Unexpected external request in controlled receiver.");
		if (request.headers.get("Authorization") !== `Bearer secret-guest-${sequence}`) return new Response(null, { status: 401 });
		if (request.url.endsWith("manifest.json")) return Response.json({ endpoints: { demo: "/agents/demo" } });
		if (request.url.endsWith("agent-card.json")) return Response.json(AgentCard.toJSON(AgentCard.fromJSON({
			name: "Controlled Agent", description: "Client delivery fixture", version: "1",
			supportedInterfaces: [{ url: local.url + "/agents/demo/", protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
			capabilities: { streaming: true }, defaultInputModes: ["text/plain"], defaultOutputModes: ["text/plain"], skills: []
		})));
		const message = { messageId: "reply", role: "ROLE_AGENT", contextId: body?.params.message.contextId ?? "ctx", parts: [{ text: "Controlled reply" }] };
		const result = { jsonrpc: "2.0", id: body?.id, result: { message } };
		if (body?.method === "SendStreamingMessage") {
			return new Response(`data: ${JSON.stringify(result)}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
		}
		return Response.json(result);
	}));
});
afterEach(() => { app?.unmount(); app = undefined; vi.unstubAllGlobals(); });

describe("client credential delivery (controlled receiver, not SDK authentication)", () => {
	it.each(["send-message", "streaming-message"] as const)("sends discovery and %s through the real A2A client using the same guest", async (requestMode) => {
		let output = "";
		await expect(runHeadless({ ...options, requestMode, responseMode: "message-output", message: "hello", readMessageFromStdin: false }, {
			stdout: { write: (text) => { output += text; } }, stderr: { write: () => undefined }
		})).resolves.toBe(0);
		expect(output).toContain("Controlled reply");
		expect(calls.filter((call) => call.url.startsWith(local.url)).every((call) => call.authorization === "Bearer secret-guest-1")).toBe(true);
		expect(sequence).toBe(1);
		expect(JSON.stringify(state.logs)).not.toContain("secret-guest");
	});

	it("connects and sends interactively without restoring another caller's context", async () => {
		app = render(<ChatApp options={options} />);
		await vi.waitFor(() => expect(app!.frames.join("\n")).toContain("Connected to"));
		app.stdin.write("hello");
		await vi.waitFor(() => expect(app!.lastFrame()).toContain("hello"));
		app.stdin.write("\r");
		await vi.waitFor(() => expect(calls.some((call) => call.body?.method === "SendMessage")).toBe(true));
		const request = calls.find((call) => call.body?.method === "SendMessage")!;
		expect(request.authorization).toBe("Bearer secret-guest-1");
		expect(request.body!.params.message.contextId).not.toBe("someone-elses-context");
		expect(JSON.stringify(state.logs)).not.toContain("secret-guest");
	});

	it("shows known expiry and creates no new identity until the explicit UI command", async () => {
		state.values.set(sessionKey, JSON.stringify({ version: 1, sessionId: "00000000-0000-4000-8000-000000000099", token: "expired-secret", expiresAt: "2026-01-01T00:00:00Z" }));
		app = render(<ChatApp options={options} />);
		await vi.waitFor(() => expect(app!.frames.join("\n")).toContain("guest session has expired"));
		expect(calls).toHaveLength(0);
		app.stdin.write("/session new default-localhost-8000");
		await vi.waitFor(() => expect(app!.lastFrame()).toContain("/session new default-localhost-8000"));
		app.stdin.write("\r");
		await vi.waitFor(() => expect(app!.frames.join("\n")).toContain("Connected to"));
		expect(sequence).toBe(1);
		expect(app.frames.join("\n")).toContain("previous session's conversations");
		expect(calls[0].authorization).toBeNull();
	});
});
